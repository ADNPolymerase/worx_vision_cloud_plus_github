# Publishing Checklist

Before the first GitHub release:

1. Create a repository, for example `Worx-Vision-Cloud-PLUS`.
2. If the URL differs, update `documentation` and `issue_tracker` in `custom_components/worx_vision_cloud/manifest.json`.
3. If you want code ownership, add your GitHub username to `codeowners` in `manifest.json`.
4. Check that no private files were added:

   ```bash
   git status --short
   git grep -n "Bearer\|access_token\|refresh_token\|eyJ\|password\|secret\|latitude\|longitude"
   ```

5. Tag the first release:

   ```bash
   git tag v0.1.0
   git push origin main --tags
   ```

6. Add the repository to HACS as a custom integration repository.
