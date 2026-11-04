---
title: Create and listen
nav_order: 5
---

# Your first week with the audio library

Once IT has installed the application, everyone uses the company URL in a
browser. Employees do not need GitHub, Docker, Python or Azure access.

## Administrators: set up the audience

1. Sign in with Microsoft using an account in the configured administrator group.
2. Open **Admin → Entra groups** and give your team groups readable names.
   Groups appear when their members sign in.
3. Ask prospective creators to sign in once, then set their role under
   **Admin → Users**.
4. Leave other employees as viewers. Their Entra group memberships determine
   which libraries they can access.

Each Entra group has one library. An empty library appears after its first
podcast is created; viewers see it after a podcast is published.

New accounts are active automatically. To stop someone using the application,
open **Admin → Users → the person**, clear **Active** and save. This blocks
new sign-ins and existing sessions on their next application request. Checking
**Active** again restores access without changing their role or group membership.
The user list also shows and filters by Active status. Changes are audited.

## Staying signed in

Sessions end after **two hours without activity**, or **eight hours after sign-in**,
whichever comes first. Browsing, interacting with the page and playing audio
count as activity. An idle open tab or background progress polling does not.
Audio playback sends an activity check about once a minute, including while the
player is in a background tab when the browser permits it.

Microsoft tokens renew silently while the application session is valid. Your
organization's Microsoft security policy or a revoked credential may require
sign-in earlier. After upgrading from the older one-hour sessions, sign out and
in once so a real local session can obtain its refresh credential.

Deactivation blocks new application and media requests. It cannot recall audio
already downloaded, including bytes buffered by the player.

## Creators: turn a document into a podcast

1. Open a library and choose **New podcast**. For a group without podcasts,
   use **Libraries → New library**.
2. Give it a title and explain the audience and what they should learn.
3. Upload source documents or add public HTTP/HTTPS links. Upload internal
   documents; links requiring company sign-in are not fetched with your account.
4. Choose the narrator and wording settings. **Review transcripts before recording**
   is selected by default; check source accuracy before approving publication.
5. Start creation and follow progress. You can close the page and find it again
   through **History**.
6. Read the transcripts and review notes. Edit where needed, then select
   **Approve & record audio**.
7. Once ready, open the podcast, listen and share its company URL with colleagues
   who have library access.

Real generation and recording incur charges from your configured AI services.
Editing text or updating a title does not itself call a model.

## Listeners: press play

Sign in with Microsoft, open a library and choose a podcast. Search helps you
find something relevant. Listen episode by episode, change playback speed or
read the transcript alongside the audio. The layout works on desktop and mobile.

You see ready podcasts in your groups, including podcasts an administrator has
shared into those libraries. A shared URL does not grant access on its own.

## Keep the library useful

Creators in the owning library can update a podcast's title, description and
cover. Editing a published transcript removes it from the listener library
until the changes are approved and recorded again. Only changed episodes need
new audio.

Administrators can share a published podcast through **Admin → Podcasts →
Additional libraries**. Sharing grants listening access; management remains
with the owning library. Removing access blocks new page/media requests.
Already downloaded or buffered audio cannot be recalled.

For run states, retries, transcript editing and audit details, see the
[operator reference](operator-guide.md).
