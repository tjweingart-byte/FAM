# Staging, and keeping old versions of the app working

Staging is a second FAM deployment that every change reaches before
production. Changes are tested there, it is where growth is stress-tested, and
once the app is in the App Store it is where changes collect before they go to
production in batches. It cannot spend money, and the server is held, in CI,
to every client version still installed. PROBLEMS.md §172 has the history.

```
feature branch --PR--> staging --(batched PR)--> Main
                          |                        |
                    fam-staging (Render)      fam (Render, production)
                    zero spend                real keys
                    TestFlight builds         App Store builds
```

## Setting it up (once)

1. **Create the `staging` branch** from `Main` and push it:

       git fetch origin Main
       git push origin origin/Main:refs/heads/staging

2. **Create both services from the blueprint.** In Render: Blueprints → New
   Blueprint Instance → this repo. It reads `render.yaml` and creates
   `fam-staging` (following `staging`) next to `fam` (following `Main`), each
   with its own disk. A service created by hand in the dashboard gets **no
   disk**, and every deploy then erases its accounts (DEPLOY.md).
   **Read Render's preview before applying.** Render matches services by
   name. The preview must show `fam-staging` as new and must not propose a
   second production service. That can happen if the existing `fam` was made
   by hand rather than from this file. The blueprint also pins `fam` to
   `branch: Main` and sets `FAM_ENV=production` on it. If production follows a
   different branch today, applying the blueprint moves it to `Main`.

3. **Give staging its own admin credentials**, when Render asks:
   `FAM_ADMIN_ACCOUNTS` and `FAM_ADMIN_TOKEN`. Use different values from
   production's. Give it nothing else. Every other key is left out on purpose.

4. **Check it:**

       curl -s https://<staging-host>/api/health | python -m json.tool | grep -A12 '"environment"'
       python tools/storage_doctor.py --url https://<staging-host>

   `environment` must say `"name": "staging"`, `"zero_spend": true`,
   `"network_guard": true`. `storage_doctor` must exit 0.

5. **Protect the branches** (GitHub → Settings → Branches), since only a person
   can do this: on `Main`, require a pull request and a passing `CI` check. Make
   `staging` the base of every feature PR.

6. **Point TestFlight builds at staging** when the iOS app exists. The API base
   URL is a build setting: staging for TestFlight, production for the App
   Store (IOS_APP.md).

### How it was actually set up (09/10)

`fam` predates the blueprint (it was made by hand, and Render's Blueprints
page is empty), so applying `render.yaml` would have tried to create a second
production service. `fam-staging` was therefore also made by hand
(**New -> Web Service**), copying its block in `render.yaml`: branch
`staging`, Docker, Starter, health check `/api/health`, auto-deploy on
commit, a 1 GB disk mounted at `/data`, and only `FAM_ENV=staging`,
`UVICORN_LOOP=asyncio`, `FAM_ADMIN_ACCOUNTS` and `FAM_ADMIN_TOKEN`. Its first
`/api/health` read `zero_spend: true`, `network_guard: true`, and every
database `durable`.

**What that means:** neither service follows `render.yaml`. A change to
either block there does nothing until it is made in the dashboard as well.
`WAITLIST` is unset on staging, so the app is open there; set `WAITLIST=1` to
test the closed app as production runs it. Staging's accounts are its own:
sign up there once (Sign Up goes to `/waitlist`; an admin email is never put
in line).

## Zero spend: what staging can and cannot do

`FAM_ENV=staging` turns on `spend_guard.py`, and no other setting can turn it
off. It works in two layers:

1. **Every paid credential is removed** and every paid switch is forced off,
   before settings are read. The removed credentials are Anthropic, Exa, GNews,
   API-Sports, Finnhub, Gemini and RunPod. The switches forced off are GDELT,
   Polymarket, the live-facts providers, thumbnails, prefetch and the remote
   voice. `FAM_SECRETS` is never consulted. A key that reaches staging anyway
   is removed and named in `/api/health` under `credentials_removed`.
2. **No connection leaves the machine.** Python's socket refuses every address
   that is not loopback, whatever library asks. This catches a provider added
   later that nobody listed. Each refused address is counted in
   `blocked_connections`.

**Staging still makes episodes.** A search on staging runs the real pipeline:
sentence splitting, pacing, raw-PCM streaming, the cache lookups, the player,
captions, saving and sharing. The writer is the built-in sample script and the
voice is a placeholder tone. Everything around an episode also runs for real:
accounts, myFAM rails, DailyFAM mixes, social, sharing, quotas, the admin page
and storage.

**What staging cannot tell you:** whether the writing is good, whether the
brief is right, real time-to-first-word, and how the voice sounds. Those spend
money, so they are tested deliberately and away from staging:

* `python write.py "<query>" --minutes 3` on your own machine with your own key
  is the loop for writing quality (CLAUDE.md).
* **Replay real episodes on staging.** Kept episodes (script, title, sources and
  audio) can be copied from production into staging's cache, where they replay
  exactly as they first played and cost nothing:

      python tools/replay_episodes.py --from https://<prod-host> --to https://<staging-host>
      python tools/replay_episodes.py --from ... --to ... --limit 25 --yes

  `author` is left behind, and so is everything else about a listener. Staging
  refuses an import unless it is zero spend, so a mistyped `--to` does nothing.
  The copied episodes keep their original dates, so an old answer is never
  presented as new.

## Old versions of the app keep working

Once FAM is in the App Store, older builds stay on people's phones. The server
must keep working for every one of them. That promise is kept in code:

* **Every client names itself** in `X-FAM-Client` (`web/2026.09.29`,
  `ios/1.0.0+12`). The web page does it for every `/api/` call.
* **`releases/registry.json`** lists every shipped client with a status:

  | status | what the server does |
  |---|---|
  | `supported` | serves it; nothing said |
  | `deprecated` | serves it, with `X-FAM-Client-Status: deprecated` on every response so the app can suggest an update |
  | `retired` | answers `426` with a sentence and the store link, except `/api/health` and `/api/client-status` |

  A version the registry does not list, or a request with no header (a
  TestFlight build, a simulator), is served as before. Unknown versions are
  never refused.
* **Every release has a contract** (`releases/contracts/`): every `/api/` path
  its code calls, and the JSON shape of each GET it makes.
  `tests/test_client_contracts.py` replays every non-retired contract against
  the current code on every push. Removing or renaming a route or field that an
  installed app reads fails CI, and the failure names the release and the key.
* **Web releases are kept whole** in `releases/web/<version>/` and served at
  `https://<host>/v/<version>/`, against the same server. To test an old
  version on staging, open that URL and use it. Kept files are checksummed, so
  editing one fails CI.
* **iOS builds are binaries**, so they are not stored in git. App Store Connect
  keeps every uploaded build. TestFlight builds expire after 90 days, so keep
  each shipped `.ipa` or `.xcarchive` as an asset on a GitHub Release tagged
  `ios-<version>` if it must stay installable for testing. The registry
  records the build number.

### Cutting a release

For each App Store submission, and for any web version worth keeping:

    python tools/cut_release.py --platform ios --version 1.0.0 --build 12 --source <path to the Swift code>
    python tools/cut_release.py --platform web                  # today's web client, as a kept copy

Run it from the commit the build was made from, and commit what it writes.
Contracts are never overwritten without `--force`.

### Rules for changing the API

1. **Add, never remove or rename.** A new field is invisible to old clients. A
   missing field crashes one.
2. **Never change what an existing field means or its type.** Add a new field
   and let both coexist.
3. **A new shape goes under `/api/v2`.** Every endpoint is already reachable at
   `/api/v1/...` (the shipped iOS app must use that form), so `/api/v2` can
   change shape while v1 keeps its promise.
4. **Build the update screen into iOS 1.0.** On launch, the app asks
   `/api/client-status` and handles `426`. It cannot be added to builds that
   have already shipped.
5. **Retire last, and only when nobody is left.** `/api/health` → `clients.seen_since_boot`
   says who is still calling. Retiring a release is
   `python tools/cut_release.py --status ios 1.0.0 retired`, and it is the
   only case where a contract stops being checked.

## Promoting staging to production

1. Merge feature PRs into `staging`. Each one deploys to `fam-staging`.
2. Test there: the checklist below, plus whatever the changes touch.
3. **Back up production's `/data`** (Render → `fam` → Disks → snapshot) before
   any change that alters a database. Render can roll back the code in one
   click; it cannot roll back a changed database.
4. Open one PR from `staging` into `Main`, which is the batch. CI must be green,
   including the client contracts.
5. After it deploys, check production's `/api/health` → `build.commit`, which
   should be the merge commit.

Once the app is in the App Store, set `autoDeploy: false` on `fam` in
`render.yaml` and deploy production by hand, so a merge and a release are
separate decisions.

### Staging checklist

* `/api/health`: `environment.zero_spend: true`, `network_guard: true`,
  `blocked_connections` has nothing unexpected.
* Sign up, sign in, sign out. (No "continue as guest": the button is withdrawn
  until FAM is public, §197.)
* Search: the loading steps, the sample script plays, the transport works,
  captions follow.
* myFAM rails draw; a tile plays; Go Deeper; the post-episode grid.
* DailyFAM: create a mix, copy a public one.
* Social: follow, message, VIBE!, share link `/s/<id>`.
* Each supported kept web release at `/v/<version>/` loads and plays.
* Replayed episodes (if any) play in their real voice from kept audio.

## Stress-testing growth

* Load-test staging rather than production. Zero spend makes it free: the
  sample script costs nothing and there is no GPU to rent. It exercises the
  server, the databases and streaming, not the model or the voice.
* **Know the ceiling going in:** every store is SQLite on one attached disk, so
  a service is one machine and cannot be scaled out by adding instances. The
  first thing a load test will find is that machine's limit. Moving the stores
  to a database server is what raising it means.
* Load-testing real generation is a separate, deliberate test with its own
  capped key, never staging's.
* **How:** `LOAD_TESTING.md` - the shapes (smoke, ramp, spike, soak,
  breakpoint), the budgets a run is judged on, the GitHub "Load test"
  workflow, and what the first runs found (§240): one process tops out
  between 250 and 500 signed-in listeners.
