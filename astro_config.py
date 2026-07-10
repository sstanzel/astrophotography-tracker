"""
astro_config.py - shared configuration for the astrophotography tracker scripts.

Capture-library locations are the one thing that varies between machines (and
between users), so they live in config.toml next to these scripts. Everything
else - the _organization folder, locations.toml, plans.toml, the target-folder
registry - is found RELATIVE to where these scripts live, so nothing else needs
to be configured.

    config.toml          <- edit this to add/remove/move libraries
    astro_config.py      <- this file (shared by all the scripts)
    ingest.py, populate_notes.py, clean_processing.py, validate.py, ...

Used by every script that touches the libraries:
    import astro_config
    libs = astro_config.load_libraries()          # [{'id','path','label','role'}]
    loc  = astro_config.org_path("locations.toml")
"""
import os
import re

# This file lives in  {library}/_organization/tracker/ , so the
# _organization folder is its parent directory and needs no configuration.
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ORG_DIR = os.path.dirname(SCRIPT_DIR)                 # .../_organization
DEFAULT_CONFIG = os.path.join(SCRIPT_DIR, "config.toml")

VALID_ROLES = ("working", "archive")


def org_path(*parts):
    """Build a path inside the _organization folder.
    e.g. org_path('locations.toml'), org_path('target folders')."""
    return os.path.join(ORG_DIR, *parts)


def load_libraries(config_path=None):
    """Parse config.toml and return a list of library dicts, in file order:
        [{'id': str, 'path': str, 'label': str, 'role': 'working'|'archive'}, ...]

    Existence of each path is NOT checked here - callers skip unmounted
    libraries themselves, so a library stays configured even when its drive is
    disconnected. Raises SystemExit with a clear message on a missing or empty
    config file."""
    path = config_path or DEFAULT_CONFIG
    if not os.path.exists(path):
        raise SystemExit(
            f"Config file not found:\n  {path}\n\n"
            f"Create it with one [[library]] block per capture library, e.g.\n"
            f'  [[library]]\n  id   = "stream"\n  path = "/Volumes/.../My Library"')

    blocks = []
    current = None
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line == "[[library]]":
                current = {}
                blocks.append(current)
                continue
            if line.startswith("["):
                # A different section (e.g. [mirror]) — stop capturing keys into
                # the last library block, otherwise its path gets overwritten.
                current = None
                continue
            m = re.match(r'(\w+)\s*=\s*"(.*?)"\s*(?:#.*)?$', line)
            if m and current is not None:
                current[m.group(1)] = m.group(2)

    libs = []
    for i, b in enumerate(blocks):
        p = (b.get("path") or "").strip()
        if not p:
            continue                                  # incomplete block, skip
        lib_id = (b.get("id") or f"lib{i+1}").strip()
        role = (b.get("role") or "working").strip().lower()
        if role not in VALID_ROLES:
            role = "working"
        libs.append({
            "id": lib_id,
            "path": p,
            "label": (b.get("label") or lib_id).strip(),
            "role": role,
        })

    if not libs:
        raise SystemExit(
            f"No usable [[library]] entries in {path} - "
            f"each block needs at least a path.")
    return libs


def mirror_path(config_path=None):
    """Return the offline-mirror directory from config.toml's [mirror] path.

    The [mirror] section is optional; refresh.py copies the dashboard + xlsx
    there (e.g. a OneDrive folder) so they are available when the libraries are
    dismounted.

    Args:
        config_path: path to config.toml (defaults to the one next to the scripts).

    Returns:
        The mirror directory path, or None if no [mirror] path is configured.
    """
    path = config_path or DEFAULT_CONFIG
    if not os.path.exists(path):
        return None
    in_mirror = False
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("["):
                in_mirror = line == "[mirror]"
                continue
            if in_mirror:
                m = re.match(r'path\s*=\s*"(.*?)"', line)
                if m:
                    return m.group(1)
    return None


if __name__ == "__main__":
    # Quick self-check: print the resolved configuration.
    print(f"SCRIPT_DIR : {SCRIPT_DIR}")
    print(f"ORG_DIR    : {ORG_DIR}")
    print(f"config     : {DEFAULT_CONFIG}")
    print("libraries:")
    for L in load_libraries():
        exists = "  (mounted)" if os.path.isdir(L["path"]) else "  (NOT mounted)"
        print(f"  [{L['id']}] {L['role']:8} {L['path']}{exists}")
