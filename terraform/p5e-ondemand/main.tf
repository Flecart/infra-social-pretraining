###############################################################################
# Network: use the default VPC/subnet unless a subnet is supplied.
###############################################################################
data "aws_vpc" "default" {
  default = true
}

data "aws_subnet" "selected" {
  count = var.subnet_id == null ? 0 : 1
  id    = var.subnet_id
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

locals {
  subnet_id = var.subnet_id != null ? var.subnet_id : tolist(data.aws_subnets.default.ids)[0]
}

# The data volume must live in the same AZ as the instance.
data "aws_subnet" "this" {
  id = local.subnet_id
}

###############################################################################
# AMI: latest AWS Deep Learning OSS AMI (Ubuntu, NVIDIA driver + PyTorch).
###############################################################################
data "aws_ami" "dlami" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["Deep Learning OSS Nvidia Driver AMI GPU PyTorch *Ubuntu 22.04*"]
  }

  filter {
    name   = "architecture"
    values = ["x86_64"]
  }
}

###############################################################################
# SSH key + security group
###############################################################################
resource "aws_key_pair" "this" {
  key_name   = "${var.name}-key"
  public_key = file(pathexpand(var.public_key_path))
}

resource "aws_security_group" "this" {
  name        = "${var.name}-sg"
  description = "SSH access for ${var.name}"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description = "SSH"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.allowed_ssh_cidr]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${var.name}-sg" }
}

###############################################################################
# Persistent /home volume — this is the part that SURVIVES terraform destroy.
# It holds every user's home directory (code, data, checkpoints). prevent_destroy
# makes Terraform refuse to delete it, and the attachment's skip_destroy means
# terminating the instance never touches the volume. User *accounts* are
# recreated declaratively each boot (see var.users + user_data) so the homes on
# this volume always have matching, stable-UID owners.
###############################################################################
resource "aws_ebs_volume" "home" {
  availability_zone = data.aws_subnet.this.availability_zone
  size              = var.data_volume_size
  type              = "gp3"

  tags = { Name = "${var.name}-home" }

  lifecycle {
    # Growing `size` is an in-place update (safe). Shrinking would force a
    # replace, but prevent_destroy blocks that, so your data is never wiped.
    prevent_destroy = true
  }
}

resource "aws_volume_attachment" "home" {
  device_name = var.data_volume_device
  volume_id   = aws_ebs_volume.home.id
  instance_id = aws_instance.this.id

  # Critical: on instance termination, detach in state only — never destroy the
  # volume or block teardown on a force-detach.
  skip_destroy = true
}

###############################################################################
# Persistent primary network interface (ENI). Holds a STABLE private IP, the
# security group, and the Elastic IP — all of which therefore survive ups/downs.
# delete_on_termination=false on the attachment means terminating the instance
# only detaches it; the ENI (and its private IP) lives on and is reattached to
# the next instance on `make up`.
###############################################################################
resource "aws_network_interface" "this" {
  subnet_id       = local.subnet_id
  security_groups = [aws_security_group.this.id]
  description     = "${var.name} persistent primary ENI"

  tags = { Name = "${var.name}-eni" }

  lifecycle {
    prevent_destroy = true
  }
}

###############################################################################
# The instance. It always exists while applied; the `running` variable
# starts/stops it without destroying the root volume (data preserved).
# Network config lives on the persistent ENI above, not on the instance.
###############################################################################
resource "aws_instance" "this" {
  ami           = data.aws_ami.dlami.id
  instance_type = var.instance_type
  key_name      = aws_key_pair.this.key_name

  # Attach the persistent ENI as the primary interface (eth0). Detach-only on
  # termination so the stable private IP + EIP are preserved.
  network_interface {
    network_interface_id  = aws_network_interface.this.id
    device_index          = 0
    delete_on_termination = false
  }

  # On every boot: mount the persistent volume at /home, then (re)create the
  # declared users with pinned UIDs and inject their SSH keys (see template).
  user_data = templatefile("${path.module}/user_data.sh.tftpl", {
    volume_id        = aws_ebs_volume.home.id
    users            = var.users
    default_user     = "ubuntu"
    default_user_key = file(pathexpand(var.public_key_path))
  })

  root_block_device {
    volume_size           = var.root_volume_size
    volume_type           = "gp3"
    delete_on_termination = true
  }

  # Only attach to a reservation when one is supplied.
  dynamic "capacity_reservation_specification" {
    for_each = var.capacity_reservation_id == null ? [] : [1]
    content {
      capacity_reservation_target {
        capacity_reservation_id = var.capacity_reservation_id
      }
    }
  }

  tags = { Name = var.name }

  # Toggling `running` should not force a replace.
  lifecycle {
    ignore_changes = [ami]
  }
}

# Drives the start/stop toggle.
resource "aws_ec2_instance_state" "this" {
  instance_id = aws_instance.this.id
  state       = var.running ? "running" : "stopped"
}

###############################################################################
# Stable public IP. The EIP is associated with the persistent ENI (not the
# instance), so both the allocation AND the association survive ups/downs — the
# public IP keeps routing to the same private IP, which reattaches to the new
# instance. prevent_destroy keeps the address from being released by accident.
###############################################################################
resource "aws_eip" "this" {
  domain = "vpc"
  tags   = { Name = "${var.name}-eip" }

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_eip_association" "this" {
  allocation_id        = aws_eip.this.id
  network_interface_id = aws_network_interface.this.id
}
