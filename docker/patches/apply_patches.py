"""Apply patches to the cloned cephci repository."""
import sys
from pathlib import Path


def patch_get_image(cephci_dir: str) -> bool:
    """Patch _get_image to handle duplicate image names in OpenStack Glance."""
    target = Path(cephci_dir) / "compute" / "openstack.py"
    if not target.exists():
        print(f"  ⚠️  File not found: {target}")
        return False

    content = target.read_text()

    old_block = (
        '        if len(images) != 1:\n'
        '            raise ExactMatchFailed(\n'
        '                f"Found none or more than one image resource with name: {name}"\n'
        '            )\n'
        '\n'
        '        return images[0]'
    )

    new_block = (
        '        if len(images) == 1:\n'
        '            return images[0]\n'
        '\n'
        '        if len(images) > 1:\n'
        '            active = [i for i in images if i.extra.get("status", "").lower() == "active"]\n'
        '            if active:\n'
        '                active.sort(key=lambda i: i.extra.get("created", ""), reverse=True)\n'
        '                LOG.info(f"Multiple images for \'{name}\', using most recent: {active[0].name}")\n'
        '                return active[0]\n'
        '            return images[0]\n'
        '\n'
        '        raise ExactMatchFailed(\n'
        '            f"Found none or more than one image resource with name: {name}"\n'
        '        )'
    )

    if old_block not in content:
        if "if len(images) == 1:" in content:
            print("  ✅ _get_image already patched")
        else:
            print("  ⚠️  Pattern not found — file may have changed upstream")
        return False

    content = content.replace(old_block, new_block)
    target.write_text(content)
    print("  ✅ Patched: compute/openstack.py (_get_image multi-match)")
    return True


if __name__ == "__main__":
    cephci_dir = sys.argv[1] if len(sys.argv) > 1 else "./cephci"
    print("Applying patches to CephCI...")
    patch_get_image(cephci_dir)
