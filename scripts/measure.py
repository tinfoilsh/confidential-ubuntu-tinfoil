#!/usr/bin/env python3
"""Build the deployment a client verifies an IGVM-booted guest against.

Runs boot-shim over the published guest image and writes tinfoil-deployment.json.
The measurement is whatever the builder reports, never recomputed here: there is
no second implementation to drift from the one the launch host runs.
"""
import base64
import hashlib
import json
import pathlib
import subprocess
import sys


def field(config: str, name: str) -> str:
    for line in config.splitlines():
        if line.startswith(f"{name}:"):
            return line.split(":", 1)[1].strip().strip('"')
    sys.exit(f"{name} missing from tinfoil-config.yml")


def igvm_cmdline(cmdline: str) -> str:
    """The launcher drops the firmware PCI options and appends its own, last.
    A different position is a different command line, so a different measurement."""
    kept = [w for w in cmdline.split() if not w.startswith(("pci=", "pcie_ports="))]
    return " ".join(kept + ["pci=noacpi", "pcie_ports=compat"])


def main() -> None:
    builder, kernel, initrd, manifest_path = sys.argv[1:5]
    config_bytes = pathlib.Path("tinfoil-config.yml").read_bytes()
    config = config_bytes.decode()
    manifest = json.loads(pathlib.Path(manifest_path).read_text())

    for name, key in ((kernel, "kernel"), (initrd, "initrd")):
        got = hashlib.sha256(pathlib.Path(name).read_bytes()).hexdigest()
        if got != manifest[key]:
            sys.exit(f"{name}: manifest says {manifest[key]}, downloaded {got}")
    print("kernel and initrd match the published manifest")

    cpus, memory = field(config, "cpus"), field(config, "memory")
    config_hash = hashlib.sha256(config_bytes).hexdigest()

    # Published as-is. The launcher derives the IGVM line from it, so this has
    # to be the line it expects to receive.
    firmware = (
        "readonly=on pci=realloc,nocrs modprobe.blacklist=nouveau "
        "nouveau.modeset=0 root=/dev/mapper/root "
        f"roothash={manifest['root']} tinfoil-config-hash={config_hash}"
    )
    measured = igvm_cmdline(firmware)
    print("measuring:", measured)

    launch = {}
    for platform in ("snp", "tdx"):
        subprocess.run(
            [builder, f"build-{platform}", "--kernel", kernel, "--initramfs", initrd,
             "--ram", f"{memory}M", "--vcpus", cpus, "--cmdline", measured,
             "--output", f"{platform}.igvm"],
            check=True,
        )
        launch[platform] = json.loads(pathlib.Path(f"{platform}.igvm.manifest.json").read_text())

    if launch["snp"]["command_line"] != launch["tdx"]["command_line"]:
        sys.exit("the two platforms measured different command lines")
    if not launch["snp"]["command_line"].startswith(measured):
        sys.exit("the builder measured a command line it was not given")

    deployment = {
        "snp_measurement": launch["snp"]["launch"]["measurement"],
        # Zero under boot-shim: nothing extends a runtime register, everything
        # is in MRTD. Present because the predicate requires it, and inert
        # because nothing verifies TDX against this.
        "tdx_measurement": {
            "rtmr1": launch["tdx"]["launch"]["rtmr1"],
            "rtmr2": launch["tdx"]["launch"]["rtmr2"],
        },
        "vm_shape": {"cpus": int(cpus), "memory_mb": int(memory), "gpus": 0, "disks": 3},
        "cmdline": firmware,
        "hashes": manifest,
        "config": base64.b64encode(config_bytes).decode(),
        "igvm": {
            "snp_launch": launch["snp"]["launch"],
            "tdx_launch": launch["tdx"]["launch"],
            "cmdline": launch["snp"]["command_line"],
        },
    }
    pathlib.Path("tinfoil-deployment.json").write_text(json.dumps(deployment, indent=4))
    print(json.dumps({k: v for k, v in deployment.items() if k != "config"}, indent=2))


if __name__ == "__main__":
    main()
