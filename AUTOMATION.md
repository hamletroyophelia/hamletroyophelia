# Roy's public profile

The profile is for `hamletroyophelia` (GitHub user ID `119651951`). The workflow refreshes daily at **09:17 Asia/Shanghai**, equivalent to `01:17 UTC`, and can also be run manually. GitHub schedules may be delayed and inactive public repositories may have schedules disabled after 60 days.

## Public data scope

Anonymous GitHub REST reads only. No authorization header is used for data reads, even when a `GITHUB_TOKEN` exists in the environment. The token is used by Git only to write generated files into this same Profile repository.

- Original projects: publicly visible, owned by the verified account, active, non-fork; exclude the Profile repository.
- Counts: projects, cumulative stars and forks within that scope, public primary-language inventory, followers and following. Total owned public repositories includes forks and archives, excluding the Profile repository.
- Activity: 30 UTC dates including the observation date. Search counts for public authored commits, newly created PRs and Issues; exclude the Profile repository. Commit search covers indexed commits on public default branches and is not a complete contribution-calendar count. Contributions to other public repositories are included. Updated original projects use their latest push date.
- Technologies: primary-language field plus allowlisted dependencies in recognized root manifests from recent/featured public repositories, at most eight. This is evidence of project usage, not proficiency.
- Achievements: explicit repository-ID or numeric-threshold rules in `config/profile.json`; 10 rules, currently eight unlocked. These are custom milestones, not official GitHub achievements.
- Unknown metrics render as `—`/unknown. Incomplete search results do not become zero. Core repository, identity or manifest failures stop the update and preserve the previous files.

No private repositories, email addresses, server details, private chats or unpublished projects enter the snapshot. Only an allowlist of public repository metadata, numeric activity totals and recognized dependency labels is stored. Books are not included.

## Update boundaries

The workflow has top-level `permissions: {}`, with `contents: write` only for its guarded job in `hamletroyophelia/hamletroyophelia` on the default branch. It uses the built-in short-lived token and no PAT, account settings or other repository permissions. Checkout is pinned to commit `11d5960a326750d5838078e36cf38b85af677262`. It runs 33 boundary/regression tests, reads public data, then commits changed generated files. No force push. Unchanged data within a day produces no timestamp-only commit; the daily 30-date activity window advances honestly.

Only `README.md`, the three files under `data/`, and `assets/generated/` are staged by automation. The three `PROFILE-SYNC` regions are replaced; manual biography, music equipment and artwork remain intact. Generated-file deletion is restricted to the prior validated allowlist.

## Original pixel artwork

Four built-in image-generation calls produced the moonlit studio concept, empty character plate, six-pose bard-engineer sheet and 16-cell equipment/UI atlas. Prompts are in `art-prompts/`; generated sources are in `assets/ai/`; extracted original icons and frames are in `assets/art/`. No reference-author illustration, biography or logo was copied. The role is an original fantasy character, not a portrait claim.

`python3 scripts/sync_profile.py --root .` uses only the standard library. It embeds original PNG art into SVG panels, then overlays verified numbers and labels deterministically. The ornate frame uses slices to keep decorations clear of text. Changing public counts does not regenerate AI artwork or call a paid API.

`python3 scripts/build_art.py` is an optional local compositor requiring Pillow and the listed macOS fonts. It creates the 60-frame, six-second looping GIF from the generated poses and scene, adding code-rendered text, floating notes, sound bars and star pulses. The static PNG is a no-animation alternative. GitHub Actions does not invoke this local script.

## Sources

- [Public repositories API](https://docs.github.com/en/rest/repos/repos#list-repositories-for-a-user)
- [GitHub search API](https://docs.github.com/en/rest/search/search)
- [Commit search scope](https://docs.github.com/en/search-github/searching-on-github/searching-commits)
- [PR and Issue search](https://docs.github.com/en/search-github/searching-on-github/searching-issues-and-pull-requests)
- [Built-in token](https://docs.github.com/en/actions/tutorials/authenticate-with-github_token)
- [Scheduled workflow behavior](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)
