## Outcome

Describe the user-visible problem and result.

## Verification

- [ ] `ruff check .`
- [ ] `mypy`
- [ ] `pytest`
- [ ] Schema, license, and source-manifest checks
- [ ] Native CTest / real-engine tests when native or geometry code changed
- [ ] STL reload proves watertight positive volume when output changed

## Release impact

- [ ] Paint remains a strict contact mask and stays registered to pose.
- [ ] Enabled single-trunk output remains connected at the base.
- [ ] Existing outputs are replaced atomically only after validation.
- [ ] No user model, diagnostic geometry, local path, cache, or build output is included.
- [ ] New dependencies and licenses are documented.
- [ ] `CHANGELOG.md` is updated, or the change is not user-visible.
