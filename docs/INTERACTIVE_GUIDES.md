# Interactive guides

Every portal has a step-by-step guide that highlights part of the screen and explains it:

- **Customer app** (`/user`)
- **Org Admin** (`/org-admin`)
- **Super Admin** (`/superadmin`)
- **Partner Portal** (`/partner`)
- **Platform console** (`/admin`)

## How people use them

- **First visit:** the welcome guide starts on its own the first time someone opens a portal. It doesn't start while a dialog is open.
- **Guide button:** the **? Guide** button in the top bar reopens it. Its menu lists the welcome guide and the page guides, with a ✓ next to the ones already finished. **Show the welcome guide again** resets them.
- **Controls:** **Next**, **Back** and **Skip guide** buttons, or the **→** / **←** keys and **Esc**.
- **Missing parts:** if a highlighted part isn't on screen, for example because of the person's role or because there's no data yet, the step is skipped or explained in the middle of the screen.
- **Remembered per person:** finishing or skipping a guide is remembered for each person in their browser (`localStorage`). Another browser shows the welcome guide again.

## How it is built

One engine for every portal: `app/static/design/tour.js` and `app/static/design/tour.css`. Each portal's own script defines its guides:

```js
LeadTour.setUser(currentUser.email);           // remembered per person
LeadTour.define("oa-team", {
  title: "Managing your team",
  steps: [
    { route: "#team", target: '[data-tour="team-invite"]', title: "Invite people",
      body: "Send an invitation by email. They choose their own password." },
    { target: '[data-tour="team-roles"]', title: "Roles", body: "Owners and admins manage the workspace…",
      optional: true },                         // skipped when not on screen
    { title: "All set", body: "Reopen this guide from the Guide button." },  // no target = centred
  ],
});
LeadTour.mountLauncher({ container: topBarEl, tours: [{ id: "oa-basics", label: "Portal basics" },
                                                      { id: "oa-team", label: "Managing your team" }] });
LeadTour.autoStart("oa-basics");
```

### Step fields

| Field | Meaning |
|---|---|
| `target` | The part of the screen to highlight: a CSS selector (prefer a `data-tour="…"` attribute) or a function that returns the element. Leave it out for a centred message. |
| `title`, `body` | Plain text. A blank line (`\n\n`) starts a new paragraph. |
| `route` | The page to open first: a hash such as `#billing`, or a function that navigates. |
| `before` | An async function to run first, for example to open a tab. |
| `placement` | `auto` (default), `top`, `bottom`, `left`, `right` or `center`. |
| `optional` | Skip the step silently if its target isn't on screen. |
| `wait` | How long to wait for the target, in ms. The default is 3000 when the step has a `route` or `before`, otherwise 800. |

### Changing a guide

- **Edit the steps** in the portal's script. Keep the text short and say what each part is for.
- **Bump the `?v=` tag** of the changed script in the portal's HTML so browsers load the new version.
- **New screens** need a `data-tour` attribute on the element a step points at, so the guide keeps working when the layout changes.
