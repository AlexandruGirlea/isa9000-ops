---
title: Make it yours
nav_order: 4
---

# Your name. Your logo. Your audio library.

isa9000-ops is white-label software. Employees can see **Contoso Learning**,
your team logo and your colors throughout the experience. The default footer
is simply **Powered by isa9000**.

## 1. Edit the brand settings

In your company copy, edit the existing sections of `config/isa9000.toml`.
Do not append a second copy of an existing TOML section.

```toml
[branding]
brand = "Contoso Learning"
logo = "logo.svg"
accent_color = "#0F6CBD"
powered_by = "Powered by isa9000"

[presentation_website]
title = "Contoso Learning"
heading = "A little listening. A lot to learn."
description = "Knowledge and ideas from across our teams."
```

Keep the file's other settings. Set the presentation title as well as the brand
so an existing title does not keep the old name on the sign-in page.

## 2. Add your logo

Save your company's SVG as **`config/logo.svg`**. The Azure deployment expects
that exact filename. Leave `logo = ""` to use the default isa9000 logo.

The portal, sign-in page, admin title, favicon, podcast pages and newly generated
cover art use the configured branding. Existing audio and cover images do not
change when you change the brand.

## 3. Preview and deploy

Run `make up` and open <http://localhost:8000>. Check the sign-in page,
libraries and a podcast in light and dark modes.

If you set `ISA9000_CONFIG_FILE` in your shell or private local settings, that file supplies the
local brand settings instead of `config/isa9000.toml`; update the file you
actually mount.

For Azure, commit the nonsecret brand settings and logo to your company
repository and use the approved [deployment process](azure.md). Pulumi mounts
them at runtime; you do not need to build a custom application image just to
change your brand. Existing podcast pages need rendering again to pick up
a changed logo or page title.

## Narration and attribution

The deployment sets narration defaults. Creators can choose supported voices,
narrator names, greetings and pronunciation settings for their podcasts.
Provider, model deployments and credentials remain under IT's control.

The visible footer makes no copyright claim over your company's content.
Source and dependency license notices remain separate from the visible branding.

**Please keep the "Powered by isa9000" credit.** It links to the
[isa9000 GitHub page](https://github.com/AlexandruGirlea/isa9000) and helps more
teams find the project. It is optional: Apache-2.0 does not require it, and
`powered_by = ""` removes it.

Never put credentials in the shared brand file. Azure model mappings belong in
stack settings; local overrides belong in the private TOML described in the
[installation guide](getting-started.md#5-try-real-audio-with-approved-ai-services).
