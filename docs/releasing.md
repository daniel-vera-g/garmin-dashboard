# Releasing

Each release is a git tag `vX.Y.Z` on `main` plus a GitHub release whose notes are the
version's section of [CHANGELOG.md](../CHANGELOG.md). The dashboard header shows the
`VERSION` it was built from.

**Which number:** major = needs action from the user (config format change, removed
feature); minor = new features; patch = fixes only.

## Steps

1. On the feature branch: add the changes under a new version heading in
   `CHANGELOG.md` and set `VERSION`.
2. `./run.sh test` passes; merge to `main` (fast-forward) and push.
3. Tag and publish:

```bash
git tag -a v1.1.0 -m "v1.1.0" && git push origin v1.1.0
```

```bash
gh release create v1.1.0 --title "v1.1.0" --notes "$(awk '/^## \[1.1.0\]/{f=1;next} /^## \[/{f=0} f' CHANGELOG.md)"
```

Only tag commits that are in the published history.
