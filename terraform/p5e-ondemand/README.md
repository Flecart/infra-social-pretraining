# p5e.48xlarge on-demand

Spin a single `p5e.48xlarge` (8× NVIDIA H200) DLAMI node up and down on demand.

> ⚠️ **Cost:** ~$30–40/hour while running. There is **no smaller P5e size** — you
> get all 8 GPUs. Always `make down` or `make stop` when you're finished.

## Setup

```bash
cp terraform.tfvars.example terraform.tfvars
# edit: set allowed_ssh_cidr to "$(curl -s https://checkip.amazonaws.com)/32"
terraform init        # or: make init
```

## How persistence works — IMPORTANT

There are **two** disks:

| Disk | Mount | Survives `make down`? |
|------|-------|------------------------|
| Root volume (OS, DLAMI) | `/` | ❌ **No — rebuilt from the AMI each `up`** |
| **Persistent home volume** | **`/home`** (every user's home dir) | ✅ **Yes — survives** |

Every user's home directory — their code, data, checkpoints, dotfiles — lives on
the persistent `/home` volume. It's protected by `prevent_destroy` and a
`skip_destroy` attachment, so terminating the instance never deletes it. On the
next `make up` it's re-attached and re-mounted automatically (no reformatting).

### What's stable vs ephemeral across ups/downs

`make down` releases **only the compute instance**. Everything else persists:

| Resource | Persists across `make down`? | Notes |
|----------|------------------------------|-------|
| VPC + subnet | ✅ | Your **default** VPC — used via a data source, never created/destroyed |
| Security group + key pair | ✅ | Created once, not touched by `down` |
| **Network interface (ENI)** | ✅ | Holds the **stable private IP** + SG; detached, not deleted, on `down` |
| **Elastic IP** (public IP) | ✅ | Associated to the ENI, so the public IP stays put too |
| **/home EBS volume** | ✅ | All users' data |
| EC2 instance + root disk | ❌ | The only thing torn down; rebuilt from the AMI on `up` |

So you always SSH to the **same public IP** (`make status` shows it), the **private
IP is constant too** (held by the persistent ENI), and `/home` is always there. The
ENI is attached as the instance's primary interface (`eth0`) on each `make up` and
carries the security group, private IP, and Elastic IP — nothing network-facing
changes between sessions.

> 💸 An Elastic IP costs ~**$0.005/hr (~$3.6/month)** for the IPv4 address — AWS now
> bills all public IPv4, attached or not. Negligible next to compute, and the price
> of a stable address. Releasing the EIP (full `destroy-all`) is the only way to
> stop that charge, but then you lose the fixed IP.

### Users are declarative

User **accounts** can't live on the home volume (they're defined in `/etc`, on the
ephemeral root disk). Instead they're declared in `var.users` and **recreated
identically on every boot** with **pinned UIDs**, so the homes on `/home` always
have matching owners. Define your team in `terraform.tfvars`:

```hcl
users = [
  { name = "angelo",   uid = 1001, sudo = true, ssh_authorized_keys = ["ssh-ed25519 AAAA... angelo"] },
  { name = "emanuele", uid = 1002,              ssh_authorized_keys = ["ssh-ed25519 AAAA... emanuele"] },
]
```

Rules of the road:
- **Add/remove users here, then `make up`** — not with ad-hoc `useradd` on the box
  (an account made by hand won't come back after a teardown; only its `/home` data
  would survive, orphaned).
- **Never change a user's `uid`** once they have data — the UID is baked into file
  ownership on the persistent volume. Changing it orphans their files.
- The default **`ubuntu`** admin user (your `public_key_path` key) always works and
  is what `make ssh` uses.

## Daily use

| Action | Command | What it costs | Data |
|--------|---------|---------------|------|
| Bring up + start | `make up` | full compute (~$30–40/hr) + EBS | — |
| Stop | `make stop` | EBS + **compute if reserved** (see below) | all homes kept |
| Start again | `make start` | full compute again | — |
| Terminate instance | `make down` | only the /home EBS volume | **all homes kept**, root wiped |
| Show IP / SSH cmd | `make status` | — | — |
| Connect (as ubuntu) | `make ssh` | — | — |

Each declared user logs in with their own key: `ssh <name>@<public_dns>`.

> ⚠️ **Does `make stop` actually save money? Only for plain on-demand.** Stopping
> a *plain* on-demand instance halts the instance-hour (compute) charge — you then
> pay only EBS. **But** if you hold a **Capacity Block for ML** or an **On-Demand
> Capacity Reservation** (the usual way to even get a p5e), you keep paying the
> reservation **whether the instance is running, stopped, or absent** — stop frees
> nothing. To truly stop the bill: `make down` (plain on-demand), cancel the ODCR,
> or let the Capacity Block expire. Note also that **stopping wipes local NVMe
> instance-store scratch**; your `/home` is on EBS so it's safe. Caveat for plain
> on-demand: stopping releases scarce p5e capacity, so `start` may fail to get it back.

Plain Terraform equivalents:

```bash
terraform apply -var running=true     # up
terraform apply -var running=false    # stop, everything preserved
# terminate instance but KEEP the home volume:
terraform destroy -target=aws_ec2_instance_state.this \
                  -target=aws_volume_attachment.home \
                  -target=aws_instance.this
```

### Growing the /home volume

Safe and online — gp3 supports live resize with no downtime and no data loss:

1. Bump the size (must be **larger** — EBS can grow but never shrink):
   ```bash
   terraform apply -var running=true -var data_volume_size=1000
   ```
   This is an **in-place update**, not a replace — Terraform shows `~ size`, not
   `-/+`. The volume id stays the same and `/home` is untouched.
2. Grow the filesystem on the running box (the disk is bigger, the ext4 fs isn't yet):
   ```bash
   make ssh
   sudo resize2fs "$(findmnt -no SOURCE /home)"
   df -h /home   # confirm new size
   ```

Notes:
- Wait a few minutes between step 1 and 2 if AWS reports the volume in
  `optimizing` state (`aws ec2 describe-volumes-modifications`).
- You can only modify a given volume's size once every **6 hours**.
- Shrinking is not supported by AWS; Terraform would try to replace the volume,
  but `prevent_destroy` blocks that — so you can't accidentally wipe data.

### Deleting the home volume for good

`terraform destroy` on its own will **fail** — that's intentional: `prevent_destroy`
guards `/home`. To truly delete it (and stop the small storage charge), remove
`prevent_destroy = true` from `aws_ebs_volume.home` in `main.tf`, then run
`terraform destroy`.

## Important notes on p5e

- **Capacity:** p5e is scarce. A plain on-demand launch often fails with
  `InsufficientInstanceCapacity`. Reserve capacity via **EC2 Capacity Blocks for
  ML** (or an On-Demand Capacity Reservation) and pass its id as
  `capacity_reservation_id`.
- **Quota:** you need a non-zero service quota for *Running On-Demand P instances*
  (measured in vCPUs — p5e.48xlarge = 192 vCPUs). Request it in Service Quotas.
- **Region:** only some regions offer p5e (e.g. us-east-1, us-east-2, us-west-2).
- **stop vs down:** `make stop` keeps *both* volumes; it only frees the compute
  charge for *plain* on-demand (not under a Capacity Block/ODCR — see the billing
  warning above). `make down` terminates the instance (and wipes the root disk)
  but keeps the `/home` volume with every user's data. For plain on-demand,
  `make down` is the way to actually stop the compute bill between sessions —
  homes and declared users are restored on the next `make up`.
