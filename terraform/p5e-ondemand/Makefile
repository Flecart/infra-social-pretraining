# Convenience wrappers. Requires terraform.tfvars (see terraform.tfvars.example).
.PHONY: init up stop start down status ssh destroy-all

init:        ## one-time provider download
	terraform init

up:          ## create + start the instance (billing begins)
	terraform apply -auto-approve -var running=true

stop:        ## stop the instance. NOTE: only frees compute $ for PLAIN on-demand;
             ## under a Capacity Block / ODCR you keep paying the reservation. EBS always billed.
	terraform apply -auto-approve -var running=false

start:       ## start a previously stopped instance
	terraform apply -auto-approve -var running=true

down:        ## terminate the instance only; persistent /home volume is preserved
	terraform destroy -auto-approve \
		-target=aws_ec2_instance_state.this \
		-target=aws_volume_attachment.home \
		-target=aws_instance.this

destroy-all: ## DANGER: delete EVERYTHING incl. the /home volume and Elastic IP. Requires editing out prevent_destroy first.
	@echo "Refusing: remove 'prevent_destroy = true' from aws_ebs_volume.home AND aws_eip.this in main.tf, then run 'terraform destroy'."
	@exit 1

status:      ## show current state + connection info
	terraform output

ssh:         ## ssh into the running instance
	@$$(terraform output -raw ssh_command)
