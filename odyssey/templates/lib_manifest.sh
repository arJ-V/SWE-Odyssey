# Base-snapshot manifest, included into setup.sh and test.sh by `odyssey
# compile` and used by `odyssey validate`. Must stay byte-identical to
# odyssey.compile.tree_manifest, which writes the manifest the scripts compare
# against. The ignored names are what image builds typically drop (.git, caches,
# bytecode, macOS Finder files), so a stray one on the host cannot fail a run.

tree_manifest() {
    # tree_manifest <dir>: "<sha256>  ./path" per regular file, then
    # "link <target>  ./path" per symlink, each block sorted bytewise.
    (
        cd "$1" || exit 1
        sha=(sha256sum)
        command -v sha256sum >/dev/null 2>&1 || sha=(shasum -a 256)
        find . \( -name .git -o -name __pycache__ -o -name .pytest_cache -o -name .DS_Store -o -name '*.pyc' \) -prune -o -type f -print \
            | LC_ALL=C sort | tr '\n' '\0' | xargs -0 "${sha[@]}"
        find . \( -name .git -o -name __pycache__ -o -name .pytest_cache -o -name .DS_Store -o -name '*.pyc' \) -prune -o -type l -print \
            | LC_ALL=C sort | while IFS= read -r f; do printf 'link %s  %s\n' "$(readlink "$f")" "$f"; done
    )
}
