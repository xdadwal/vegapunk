# Security

Vegapunk runs on your own machine with access to your Docker host, so its security matters. This page says how to report a problem, what Vegapunk protects and what it does not, and how to run it safely.

## Supported versions

Vegapunk is pre-release. Only the latest release gets security fixes; today that is 0.1.0. Fixes are not backported to older versions.

## Reporting a vulnerability

Please do not open a public issue, pull request or discussion for a vulnerability.

Report it privately through GitHub: on the repository page, open the **Security** tab and choose **Report a vulnerability**. This is the only reporting channel the project uses. Include what you found, the version (`docker exec vegapunk python -m vegapunk version`), and the steps to reproduce it if you can.

Vegapunk is maintained in spare time. Reports are handled on a best-effort basis, with no guaranteed response time. You will hear back when the report has been read, and again when a fix is released.

## What Vegapunk protects and what it does not

### The Docker socket

- **The socket is full control of Docker.** Vegapunk reads Docker through the Docker socket mounted into its container. Whoever can use that socket can start any container, mount any folder of the host into it, and so act as root on the host. Mounting the socket read-only (`:ro`) does not change that.
- **Vegapunk only reads.** All its Docker calls go through one module whose only operations are reads (ping, info, version, list containers, one-shot stats, logs); a test pins that list. But this is a promise made by Vegapunk's code: if someone found a way to make Vegapunk send other requests, the socket would carry them.
- **The optional [socket proxy](docs/socket-proxy.md)** narrows this. Vegapunk no longer gets the socket, and the proxy refuses every request that is not a `GET` or `HEAD` to the parts of the Docker API Vegapunk reads. It does not make access read-only in every sense: it still lets through reads of files inside any container, every container's details (which include its environment variables), and `GET /containers/<id>/attach/ws`, which can send input to a container started with its input open. The page lists exactly what it does and does not prevent.
- The container runs as an unprivileged user (uid 10001), not root.

### Signing in

- **One admin account.** There are no other users or roles. Until the admin account is created, anyone who can reach Vegapunk can create it, so create it as soon as Vegapunk first starts.
- **Passwords** are at least 10 characters and are stored as argon2 hashes.
- **Sign-in lockout.** After 5 failed sign-ins within 15 minutes, sign-in is paused until 15 minutes after the first of those failures. The lockout is server-wide, not per address: anyone who can reach the sign-in page can lock the admin out for 15 minutes. It is not rate limiting of other requests. `python -m vegapunk reset-password` (run inside the container) sets a new password and clears the lockout.
- **Sessions.** The session cookie is a random token; the database keeps only its SHA-256 hash. A session lasts 30 days. Changing the password signs out every other session.
- **Cookie flags.** The session cookie is `HttpOnly` and `SameSite=Lax`. It is marked `Secure` only when `VEGAPUNK_SECURE_COOKIES=1` is set, which you should do behind HTTPS.
- **Cross-site requests.** Every POST, PUT or PATCH that changes something takes a JSON body, except three: starting a check, marking a finding reviewed, and stopping an answer. Those three need the header `X-Requested-With: vegapunk`, which a plain HTML form cannot send. Signing out needs neither a body nor the header. DELETE requests (removing the AI provider settings, deleting a conversation) cannot be sent by a plain HTML form or from another site, because no CORS is configured.
- **No HTTPS of its own.** Vegapunk serves plain HTTP on port 8000. Compose publishes that port on every network interface of the host.

### The AI provider and its API key

- **The API key is encrypted** (Fernet) in Vegapunk's database. The encryption key is the file `secret.key` in the data folder, with mode 0600. This keeps the API key out of a copied database. It does not protect against someone who can read the whole data folder or a full backup, because the encryption key sits beside it.
- **A missing `secret.key` is silently replaced** with a new one at start. Restoring a backup without it loses the saved API key (you enter it again). A `secret.key` that is present but not valid stops Vegapunk from starting instead of being replaced.
- **The API key is never returned by the API.** Settings shows only a hint: its last four characters, for keys of 12 characters or more. That hint is stored unencrypted in the database. Errors from the provider have the key removed before they are shown.
- **A saved key is only sent to the base URL it was saved for.** Changing the base URL requires entering the key again, and "Test connection" against a different address does not use the saved key.

### What reaches the AI provider

- **Your questions, and what the agent reads to answer them.** About the host: its name, OS, kernel, Docker version, CPU count, boot time, current and past CPU, memory and storage use, how many containers are running or need attention, and the status of Vegapunk's own capture (including its last error). About containers: their ids, names, images, states, health, status, restart counts, start times, current and past CPU and memory use, and container log lines. The daily check and the background watch send the same kinds of data. Your instructions in Settings are part of what is sent, and so is a finding's text when you open a chat from it.
- **Redaction is best effort.** Before container log text reaches the provider (container names and image names are sent as they are), Vegapunk replaces these with `[redacted]`: private key blocks (`-----BEGIN … PRIVATE KEY-----`), `Authorization:` header values and bearer tokens, the user and password in URLs (`scheme://user:password@`), JSON web tokens, tokens shaped like OpenAI (`sk-…`), GitHub (`ghp_`, `gho_`, `ghs_`, `github_pat_`), Slack (`xox…-`) and AWS access key ids (`AKIA…`), and the value after words such as `password`, `passwd`, `pwd`, `secret`, `token`, `api_key`, `access_key`, `private_key` and `credential` followed by `:` or `=`. A secret in any other shape is sent as it is. Do not use a provider you would not trust with your logs.
- Vegapunk does not read files inside containers. Docker's container details include each container's environment variables. Vegapunk reads them in memory with the rest, but never stores, shows or sends them: it keeps only the fields it uses (id, name, image, state, health, restart count, whether it ran out of memory, and its start and exit times).

### The agent

- **The agent only reads.** It has six tools: host overview, list containers, container details, container metrics, host metrics, and container logs. None of them can change anything, and a test pins the list. Every tool call passes one gate, which refuses any tool other than these six and logpose's built-in recall of an earlier tool result, and caps the lookups per answer (12 in chat, 20 in a check).
- **Text from containers is data.** The agent is told that container names, image names and log lines are never instructions. A model can still be misled by what it reads.
- **What a malicious container can do through its logs.**
  - It can bend the text of an answer or a finding (the title, diagnosis and suggested fix), because those are written by the model. Read a suggested fix before you act on it.
  - It can steer the agent to read other containers' logs, since the tools take any container name. Those containers' (redacted) log lines then reach your provider, and can be quoted in the answer or in a finding about another container.
  - A log line shown as a finding's evidence is checked to be one whole line of that container's (redacted) logs read during the same check, so evidence cannot be invented, but it can still be a line the container wrote to mislead.
  - When you open a chat from a finding, the finding's text, which a container may have influenced, is added to the agent's instructions, between two marker lines with a random value and labelled as data, not instructions. What the agent read stays part of that conversation.
  - It cannot make Vegapunk change anything on the host, because the agent has no tool that does. It cannot make the agent send anything anywhere except to your provider: the agent has no network tool, and the interface shows its text as plain text, with no links or images.

### What is stored

Everything is in the data volume (`/data` in the container):

- `vegapunk.db`: the admin account (with the password hash), session hashes, captured host and container data (names, images, states, restart counts, CPU, memory, storage history), findings with the log lines they cite as evidence (redacted), the AI provider settings with the encrypted API key and your instructions, and the list of conversations.
- `conversations.db`: the content of each conversation, including what the agent's tools returned while answering. A conversation's content, including the redacted log lines the agent read, stays in `conversations.db` until you delete the conversation. After a year without a new message Vegapunk stops showing its messages (the conversation stays listed in History), but does not erase them.
- `secret.key`: the key that decrypts the saved API key.

**Logs are not collected.** Vegapunk does not capture or keep container logs. The agent reads them live from Docker when it needs them. A finding keeps the redacted lines it cites as evidence. A chat keeps the redacted lines the agent read, as part of the conversation, until you delete it.

## Recommended deployment

- **Do not expose Vegapunk to the internet.** Keep it on your local network or behind a VPN, and use a firewall if the host is reachable from untrusted networks.
- **Use HTTPS** through a reverse proxy in front of Vegapunk, and set `VEGAPUNK_SECURE_COOKIES=1` so the session cookie is only sent over HTTPS.
- **Create the admin account right away** after the first start, and use a long, unique password.
- **Consider the [socket proxy](docs/socket-proxy.md)**, and read what it does not prevent.
- **Protect your backups.** A backup of the data volume contains `secret.key` beside the encrypted API key, so anyone with the backup can read your API key, as well as your conversations and findings. Store it like a password. See [Data and backups](docs/install.md#data-and-backups).
- **Choose your AI provider with care**, since your logs reach it (see above).

## Out of scope

These are not treated as vulnerabilities in Vegapunk:

- Anything that needs the attacker to already have the Docker socket, the data volume (or a backup of it), or a signed-in admin session. Each of these already gives full control of what Vegapunk can see or do.
- Denial of service by someone who can already sign in, and the server-wide sign-in lockout described above.
- A container's own logs bending the agent's answers or findings, as long as Vegapunk does not change anything because of them and cited evidence lines are real lines of that container's logs. A way to make Vegapunk act on the host, invent evidence, or send the saved API key somewhere other than its base URL is in scope.
- Secrets that redaction does not recognise, unless they match one of the shapes listed above.
- Reads the socket proxy is documented to let through.
