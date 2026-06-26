output "instance_id" {
  value = aws_instance.this.id
}

output "instance_state" {
  value = aws_ec2_instance_state.this.state
}

output "public_ip" {
  description = "Stable Elastic IP — same across every up/down."
  value       = aws_eip.this.public_ip
}

output "public_dns" {
  description = "Stable public DNS for the Elastic IP."
  value       = aws_eip.this.public_dns
}

output "private_ip" {
  description = "Stable private IP, held by the persistent ENI across ups/downs."
  value       = aws_network_interface.this.private_ip
}

output "eni_id" {
  value = aws_network_interface.this.id
}

output "ami_id" {
  value = data.aws_ami.dlami.id
}

output "home_volume_id" {
  description = "Persistent /home volume (survives teardown)."
  value       = aws_ebs_volume.home.id
}

output "ssh_command" {
  description = "Connect once the instance is running (stable address)."
  value       = "ssh ubuntu@${aws_eip.this.public_ip}"
}
