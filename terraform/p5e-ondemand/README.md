# p5e.48xlarge on-demand

Spin a single `p5e.48xlarge` (8× NVIDIA H200) DLAMI node up and down on demand.

> ⚠️ **Cost:** ~$30–40/hour while running. There is **no smaller P5e size** — you
> get all 8 GPUs. Always `make down` or `make stop` when you're finished.

## What this stack requests

This module launches one **`p5e.48xlarge`**:

| Spec | Value |
|------|-------|
| GPUs | 8× NVIDIA **H200** (141 GB each, 1128 GB total) |
| vCPUs / RAM | 192 vCPUs / 2 TB |
| GPU interconnect | NVLink + NVSwitch |
| On-demand price | ~**$30–40/hr** (region-dependent) |
| Sizes available | **only** the full 8-GPU node — no 1/2/4-GPU variants |

### GPU instance reference (for picking a different `instance_type`)

Approximate **on-demand** prices, largest size, `us-east-1`, Linux. These move over
time and by region — check the [EC2 pricing page](https://aws.amazon.com/ec2/pricing/on-demand/)
for exact numbers. Single-GPU `xlarge` sizes of the **G** families cost a small
fraction of these and are good for dev/inference.

| Family | GPU | Largest size | ~On-demand $/hr |
|--------|-----|--------------|------------------|
| P5e | 8× H200 | p5e.48xlarge | ~$30–40 ← **this stack** |
| P5 | 8× H100 | p5.48xlarge | ~$31–55 |
| P4 | 8× A100 | p4d.24xlarge | ~$32 |
| P3 | 8× V100 | p3.16xlarge | ~$24.48 |
| G6e | 8× L40S | g6e.48xlarge | ~$30 (1× ≈ $1.86) |
| G6 | 8× L4 | g6.48xlarge | ~$13.35 (1× ≈ $0.80) |
| G5 | 8× A10G | g5.48xlarge | ~$16.29 (1× ≈ $1.00) |
| G4dn | 1–4× T4 | g4dn.12xlarge | 1× ≈ $0.53 |

> The newest **P6** (Blackwell B200/B300/GB200) instances are typically sold via
> Capacity Blocks / reservations rather than plain on-demand. To use any of these,
> set `instance_type` in `terraform.tfvars` (and note only the P5/P4 families are
> sold exclusively as full 8-GPU nodes).

## Setup

```bash
cp terraform.tfvars.example terraform.tfvars
# edit: set allowed_ssh_cidrs (your IP "$(curl -s https://checkip.amazonaws.com)/32"
#       and/or institution ranges — see "SSH access" below)
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

## SSH access

`allowed_ssh_cidrs` (a list) controls the security-group ingress on port 22. It's
**fail-closed**: an empty list means nobody can SSH, so you must set it. Invalid
entries are rejected at plan time by a validation rule.

The committed `terraform.tfvars` allows the whois-verified **University of Oxford**
and **ETH Zurich** campus ranges:

| Institution | CIDR blocks |
|-------------|-------------|
| Oxford | `129.67.0.0/16`, `163.1.0.0/16`, `192.76.6.0/23`, `192.76.8.0/21`, `192.76.16.0/20`, `192.76.32.0/22` |
| ETH Zurich | `129.132.0.0/16`, `195.176.96.0/19`, `192.33.87.0/24`, `192.33.88.0/21`, `192.33.96.0/21`, `192.33.104.0/22`, `192.33.108.0/23`, `192.33.110.0/24` |

These cover the main campus allocations. Off-campus access (home/VPN that exits to
a non-campus IP, eduroam at other sites, satellite buildings) won't match — add
those CIDRs, or your own `/32`, to the list. To find an address's range:
`whois <ip> | grep -iE 'inetnum|netname|org-name'`.

## Important notes on p5e

- **Capacity (REQUIRED):** p5e **cannot** be launched as plain on-demand —
  `RunInstances` returns `Unsupported: The requested configuration is currently
  not supported`. You must purchase an **EC2 Capacity Block for ML**, then set
  both `capacity_reservation_id = "cr-..."` and `capacity_block = true` (the
  latter adds the required `market_type = capacity-block`). For a standard
  On-Demand Capacity Reservation instead, set the id but leave `capacity_block`
  false.
- **Quota:** you need a non-zero service quota for *Running On-Demand P instances*
  (measured in vCPUs — p5e.48xlarge = 192 vCPUs). Request it in Service Quotas.
- **Region:** only some regions offer p5e (e.g. us-east-1, us-east-2, us-west-2).
- **stop vs down:** `make stop` keeps *both* volumes; it only frees the compute
  charge for *plain* on-demand (not under a Capacity Block/ODCR — see the billing
  warning above). `make down` terminates the instance (and wipes the root disk)
  but keeps the `/home` volume with every user's data. For plain on-demand,
  `make down` is the way to actually stop the compute bill between sessions —
  homes and declared users are restored on the next `make up`.
