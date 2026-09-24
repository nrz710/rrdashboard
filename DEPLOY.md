# Launching the RosterResource dashboard (one file, all on GitHub)

You need one file: **`RRDASH_INSTALL.txt`**. It holds the whole project. You paste it into
GitHub once, press a button, and GitHub unpacks every file, runs the tests, and publishes
the dashboard at

    https://YOUR-USERNAME.github.io/YOUR-REPOSITORY/

Anyone with that link can use the dashboard, with no account, on a computer or a phone.
Everything happens in your web browser: nothing to install, no GitHub Desktop, no command
line, and no hidden folders to drag around.

It takes about 25 minutes, including about 15 minutes of waiting for the first data pull.
Button labels on GitHub change now and then; if one doesn't match exactly, look for the
closest one.

**The repository must be public.** GitHub's free plan only publishes websites from public
repositories, so anyone can also browse the repository, including the stored copies of
FanGraphs pages on its `data` branch. Every table and export credits FanGraphs, but check
FanGraphs' terms of service for public reuse before sharing the link widely.

---

## Part 1: Start clean (delete the old repository)

Deleting the repository removes every file, the old `data` branch and the old website
settings in one go. You'll recreate it with the same name, so the link stays the same.

1. Open the repository on github.com and click **Settings** (top row of tabs).
2. Scroll to the bottom, to **Danger Zone**, and click **Delete this repository**.
3. Follow the prompts: confirm, then type the repository name exactly (for example
   `nrz710/rrdashboard`) and click the final delete button. GitHub may ask for your password.

## Part 2: Create the new repository

4. Go to **github.com/new**:
   - **Repository name:** the same name as before (for example `rrdashboard`). It becomes
     the end of your link.
   - **Public**
   - Turn **Add README** on. (The installer replaces it; it just makes the next steps easier.)
5. Click **Create repository**.

## Part 3: Two settings (do these before installing)

6. **Settings → Actions → General.** Under **Workflow permissions**, choose **Read and write
   permissions**, then **Save**.
7. **Settings → Pages.** Under **Build and deployment → Source**, choose **GitHub Actions**.
   (There's nothing to save; the choice takes effect immediately.)

## Part 4: Paste the installer

8. Open **`RRDASH_INSTALL.txt`** in a plain text editor: Notepad (Windows), TextEdit (Mac), or
   just open it in your web browser. Don't use Word or Google Docs; they change quote marks.
9. Select everything (**Ctrl + A**, or **Command + A** on a Mac) and copy it (**Ctrl + C** /
   **Command + C**).
10. In your repository, click **Add file → Create new file**.
11. In the file-name box at the top, type exactly:

        .github/workflows/rrdash.yml

    GitHub turns each `/` into a folder as you type, so you'll see
    `.github / workflows / rrdash.yml`.
12. Click in the large editing area and paste (**Ctrl + V** / **Command + V**). The first line
    should start with `# RosterResource dashboard: the one workflow that runs everything`, and
    the very last line should mention `Your dashboard is live`.
13. Click **Commit changes…**, then **Commit changes** in the box that appears.

GitHub starts a short automatic run right away. It finishes in under a minute with a note
that the project isn't installed yet. That's expected.

## Part 5: Install and publish

14. Click the **Actions** tab. If GitHub asks, click the green button to enable workflows.
15. In the list on the left, click **RosterResource**.
16. On the right, above the list of runs, click the grey **Run workflow** button. In the box
    that opens, set **What to do** to `install`, then click the green **Run workflow** button.
17. Wait about 3 minutes. Click the run to watch it. The boxes **check**, **install**,
    **tests**, **site** and **deploy** turn green in turn; **refresh** shows as skipped, which is
    correct.
18. **Your link** appears under the **deploy** box (and always under **Settings → Pages**). Open
    it: you'll see the FanGraphs header, the tabs, and "No data has been published yet."
    Your repository's **Code** tab now shows all the project files.

## Part 6: Run the first data pull

19. **Actions → RosterResource → Run workflow**, leave **What to do** on `refresh`, and click
    **Run workflow**.
20. Wait about 15 minutes. It fetches 97 pages, 6 to 8 seconds apart. Before the first request
    it saves the attempt, so the 48-hour window is on record even if the job dies midway. When
    it finishes, the **site** and **deploy** boxes run by themselves.
21. Reload your link. The dashboard now shows real data.

What else you might see on a refresh run:

- **Yellow warnings on the "Publish" step:** the injury report, transaction tracker and closer
  depth chart are each pulled once from RosterResource's league-wide page instead of 30 times,
  and the tool checks that those pages cover all teams. A warning means one might not. Compare
  that tool on the dashboard's **All Teams** tab with the site; if rows are missing, see
  "Switching a tool back to per-team pulls".
- **Red X plus a new issue titled "RosterResource refresh halted":** FanGraphs refused requests
  from GitHub's servers. The tool stopped itself as designed and won't try again until you
  clear it. Read the issue before doing anything else.

From now on the refresh runs by itself every 6 hours. Nearly all of those runs just see that
48 hours haven't passed and stop without contacting FanGraphs; the website updates after
every new pull.

---

## Everything the Run workflow button can do

**Actions → RosterResource → Run workflow → What to do:**

| Task | What it does | Contacts FanGraphs? |
|---|---|---|
| `refresh` | Pull new data if the 48-hour gate allows, then update the website | Only if 48 hours have passed |
| `install` | Unpack the project files from the installer (first time, or after pasting a newer installer) | No |
| `website` | Rebuild and republish the website | No |
| `tests` | Run the automated tests | No |
| `clear-halt` | Clear the halt flag after a "refresh halted" issue (the next scheduled run then pulls) | No |
| `republish` | Rebuild the dashboard's data from the stored pages (after a parser change) | No |
| `record-prior-pull` | Log a pull made elsewhere, using the time box (the gate can only get stricter) | No |

## Day to day

- **Your link:** always under **Settings → Pages**.
- **Visitors:** each visitor's dashboard panels are kept in their own browser. They can save a
  layout to a file, load it elsewhere, and download Excel, a printable report, or CSV.
- **Editing a setting:** open the file in the repository, click the pencil icon, change it,
  then **Commit changes**. Tests and the website rerun by themselves.
- **Switching a tool back to per-team pulls:** open `rr/config.py`, click the pencil, find
  `LEAGUE_WIDE_INSTEAD_OF_PER_TEAM`, delete that tool's name from the list (for example
  `"injury-report", `), and commit. That tool goes back to 30 requests per pull.
- **Installing a newer version later:** open `.github/workflows/rrdash.yml`, click the pencil,
  select all and delete, paste the new `RRDASH_INSTALL.txt`, commit, then run the task
  `install`.
- **Preview with invented data:** **Settings → Secrets and variables → Actions → Variables →
  New repository variable**, name `SITE_DEMO`, value `true`, then run the task `website`.
  Delete the variable and run `website` again for real data.
- **Pausing the refresh:** **Actions → RosterResource → ⋯ (top right) → Disable workflow**.
  This pauses the website updates too; enable it the same way.
- **Taking the site down:** **Settings → Pages → Unpublish site**.

## Troubleshooting

- **The link shows README text instead of the dashboard:** **Settings → Pages → Source** isn't
  **GitHub Actions** (step 7). Set it, then run the task `website`.
- **No "RosterResource" in the Actions list, or it shows a red error about the file:** the paste
  was incomplete or the file name is wrong. Open `.github/workflows/rrdash.yml`, click the
  pencil, select all, delete, paste the installer again (steps 8 to 13), and commit.
- **No "Run workflow" button:** make sure you clicked **RosterResource** in the left list first.
  If there's a banner about workflows being disabled, click to enable them.
- **The install box fails at "Commit the project files" with a 403 or permission message:**
  redo step 6, then run `install` again.
- **The deploy box fails:** redo step 7, check the repository is **Public**, then run `website`.
- **The site says "No data has been published yet":** the first pull (Part 6) hasn't finished
  successfully yet.
- **Excel download does nothing:** the Excel export loads a small library from
  cdnjs.cloudflare.com the first time it's used; some work networks block that. The printable
  report and CSV downloads still work.

## What it costs: nothing

GitHub Actions and GitHub Pages are free for public repositories, and the site is only a few
megabytes.
