variable "aws_region" {
  description = "Region to launch the instance in. p5e is only available in select regions (e.g. us-east-1, us-east-2, us-west-2)."
  type        = string
  default     = "us-east-1"
}

variable "aws_profile" {
  description = "AWS CLI profile name from ~/.aws/credentials. Set to null to use environment/default credentials."
  type        = string
  default     = "emanuele"
}

variable "instance_type" {
  description = "Instance type. Defaults to the full 8-GPU H200 node (the only size offered)."
  type        = string
  default     = "p5e.48xlarge"
}

variable "name" {
  description = "Name tag / identifier for all resources."
  type        = string
  default     = "p5e-ondemand"
}

variable "running" {
  description = "true = instance started (billed for compute); false = stopped (only EBS billed, data preserved). terraform destroy removes everything."
  type        = bool
  default     = true
}

variable "public_key_path" {
  description = "Path to an SSH public key installed for the default `ubuntu` admin user (used by `make ssh`). Human users are defined separately in var.users."
  type        = string
  default     = "~/.ssh/id_ed25519.pub"
}

variable "users" {
  description = <<-EOT
    Declarative human users, recreated identically on every boot with PINNED
    UIDs so ownership of their persistent /home directories stays valid across
    teardowns. Add/remove users here (not with ad-hoc `useradd` on the box).
      - name: login name
      - uid:  fixed numeric UID (>=1000, unique). NEVER change once data exists.
      - ssh_authorized_keys: list of SSH public keys for that user
      - sudo: grant passwordless-group sudo (default false)
  EOT
  type = list(object({
    name                = string
    uid                 = number
    ssh_authorized_keys = list(string)
    sudo                = optional(bool, false)
  }))
  default = []

  validation {
    condition     = alltrue([for u in var.users : u.uid >= 1000])
    error_message = "Each user uid must be >= 1000 to avoid colliding with system accounts."
  }
}

variable "allowed_ssh_cidrs" {
  description = "List of CIDR blocks allowed to SSH in (port 22). Fail-closed: defaults to empty (no access) so you must set it explicitly."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for c in var.allowed_ssh_cidrs : can(cidrnetmask(c))])
    error_message = "Each allowed_ssh_cidrs entry must be a valid CIDR, e.g. \"129.67.0.0/16\" or \"1.2.3.4/32\"."
  }
}

variable "root_volume_size" {
  description = "Root (OS) EBS volume in GiB. This volume IS destroyed on teardown — only the OS/DLAMI lives here. All user homes are on the persistent /home volume."
  type        = number
  default     = 200
}

variable "data_volume_size" {
  description = "Persistent /home EBS volume in GiB. SURVIVES terraform destroy. Holds every user's home directory (code, data, checkpoints)."
  type        = number
  default     = 500
}

variable "data_volume_device" {
  description = "Block device name requested for the /home volume. On Nitro instances (p5e) it surfaces as an NVMe device; we mount by filesystem label so this is just the attachment hint."
  type        = string
  default     = "/dev/sdf"
}

variable "capacity_reservation_id" {
  description = "Optional On-Demand Capacity Reservation or Capacity Block ID. p5e capacity is scarce; plain on-demand launches often fail with InsufficientInstanceCapacity. Leave null to try open on-demand."
  type        = string
  default     = null
}

variable "subnet_id" {
  description = "Subnet to launch in. Leave null to use the default VPC's default subnet."
  type        = string
  default     = null
}
