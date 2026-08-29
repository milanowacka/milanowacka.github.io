# Content Manager (desktop app)

A small GUI for editing the site without using git or the command line directly.

Three buttons:
- **Pull Latest** — fetches the latest content from GitHub.
- **Preview Locally** — regenerates the HTML from `content/page-content.md` on
  your own computer and opens it in your browser. Nothing is pushed; this is
  just for checking your edits before publishing.
- **Push Changes** — commits and pushes your local edits to GitHub, which
  triggers the site to rebuild automatically (same as pushing manually today).

The app remembers the last folder you pointed it at (stored in
`~/.mila_content_manager.json`), so you only need to select the project
folder once.

## No separate git install needed

Git operations are done in-process via [dulwich](https://www.dulwich.io/), a
pure-Python git implementation — there's nothing else to install. This does
mean the app manages its own GitHub authentication rather than reusing an
existing git credential setup. It picks the auth method automatically from
the repo's `origin` URL:

**HTTPS remote** (`https://github.com/...`): the first time you Pull or Push,
a dialog asks for your **GitHub username** and a **Personal Access Token**
(create one at github.com → Settings → Developer settings → Personal access
tokens, with `repo` scope — GitHub no longer accepts your account password
for this). The token is saved for next time via the OS credential store (see
below).

**SSH remote** (`git@github.com:...` or `ssh://...`): the first time you Pull
or Push, a dialog asks you to pick your **private key file** (the one whose
matching public key you've added to your GitHub account — key generation and
adding the public key to GitHub is done by you beforehand, the app doesn't
do that part). If the key is passphrase-protected, it asks for the passphrase
once and saves it the same way as the PAT. SSH support uses
[paramiko](https://www.paramiko.org/) (vendored from dulwich's own
`contrib/paramiko_vendor.py`, since that module ships in dulwich's source but
isn't included in the published wheel — see `app/_paramiko_vendor.py`).
GitHub's own published SSH host keys are pre-seeded into `~/.ssh/known_hosts`
so a first connection doesn't fail with an unknown-host-key error; the app
never trusts a host key it wasn't given by GitHub's own docs/API.

In both cases:
- The secret (PAT or key passphrase) is saved using the operating system's
  credential store (Windows Credential Manager, via the `keyring` package)
  so it isn't sitting in a plain text file. If that's unavailable for some
  reason, the app falls back to storing it in `~/.mila_content_manager.json`
  and says so in the log.
- Use the **GitHub Access…** button any time to re-enter credentials (e.g. a
  token expired/revoked, or you want to switch keys — the app also clears
  a stored secret automatically if GitHub rejects it).

## Getting the Windows .exe

Every push that touches `app/**` or `site_generator.py` builds a Windows
executable automatically. You can also trigger a build manually:

1. GitHub repo → **Actions** → **Build Content Manager (Windows)** → **Run workflow**.
2. Once it finishes, open the run and download the `Mila-Content-Manager-Windows`
   artifact — it contains `Mila-Content-Manager.exe`.

Pillow, dulwich, keyring, paramiko and the generation logic are all bundled
into the .exe, so nothing needs to be separately installed on the artist's
machine — **except the initial clone**: the app operates on an existing
local checkout of the repo, it doesn't create one. Set that up once (e.g.
with GitHub Desktop, or `git clone` if git happens to be installed) before
handing the `.exe` over. If you're setting it up for an SSH remote, generate
the keypair and add the public key to the GitHub account before that first
run too.

Note: the keyring/Windows-credential-store integration hasn't been verified
on an actual Windows machine (this was built and tested on Linux, where that
backend isn't available) — the fallback plain-text storage means Pull/Push
will still work even if it doesn't, but it's worth confirming once on a real
Windows box.

## Running from source (for development)

```
pip install -r app/requirements.txt
python app/content_manager.py
```

## Building locally

Only useful if you're on Windows and want a local build instead of using
Actions (PyInstaller can't cross-compile — building on Linux/Mac produces a
Linux/Mac binary, not a .exe):

```
pip install -r app/requirements.txt
pyinstaller --onefile --windowed --name "Mila-Content-Manager" --paths . ^
  --collect-submodules keyring --hidden-import keyring.backends.Windows ^
  app/content_manager.py
```

The executable is written to `dist/Mila-Content-Manager.exe`.
