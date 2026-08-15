# Agent Skills distribution

The reader-facing product is a **Conversational Edition**. Its portable technical artifact is the **Reading Pack**. An Agent Skill is only an optional compatibility container around already-generated Reading Packs. It does not become canonical data, replace the Markdown pack, or create another approval boundary.

The generated directory follows the [Agent Skills specification](https://agentskills.io/specification). Reading Pack uses no scripts, executables, hooks, plugins, or `allowed-tools` declarations in this container.

## Build and check

First build and check the ordinary packs. Then build the optional container:

```sh
reading-pack build --project PROJECT --lang all
reading-pack check --project PROJECT --lang all --release
reading-pack agent-skill build --project PROJECT --release
reading-pack agent-skill check --project PROJECT --release
```

`agent-skill build` re-renders every configured language in memory and compares it with the ordinary file under `dist/`. If any ordinary pack is missing or stale, it fails before changing the Agent Skill outputs. It stages and validates the complete new directory and ZIP before replacing the prior distribution. It never writes canonical data, templates, ordinary Reading Packs, or approval records.

`agent-skill check` is read-only. It repeats project validation, re-renders and verifies the ordinary packs, and then compares the directory and ZIP with a fresh expected distribution byte for byte. `--release` also applies the same human publication gates as the ordinary release check.

## Output

One skill contains all languages configured by the project:

```text
dist/agent-skill/<slug>/
  SKILL.md
  manifest.json
  references/<output-basename>.<lang>.md
dist/<slug>-agent-skill.zip
```

Each file under `references/` is byte-identical to its corresponding ordinary Reading Pack. `SKILL.md` tells the host to use the question language when available, fall back to the primary language, read the chosen pack, and follow its `SYS` section. Web access may be used only when the host provides it and the selected Reading Pack requests it.

`manifest.json` records the skill name and the Reading Pack version, status, primary language, languages, and license. It also records relative paths, byte counts, and SHA-256 values for `SKILL.md` and every bundled Reading Pack. These hashes let a check detect byte mismatches; they are not a digital signature, identity proof, rights clearance, or publication approval.

The directory contains only the listed control files and references. The ZIP has sorted safe member paths, fixed metadata and permissions, and stored entries, so repeated builds from the same inputs are byte-identical. Build and check reject symlinks, non-regular files, traversal paths, extra files, script injection, and oversized inputs.

## Install and update

After a successful release check, use the installation method documented by the target host. Depending on the host, that usually means copying the generated `<slug>` directory into its skills directory or importing the ZIP. Install the directory as a unit; do not upload only `SKILL.md`, because its references and manifest are part of the distribution.

There is no universal one-click installation URL. Agent Skills defines the directory format, while discovery, upload, filesystem locations, permissions, and updates remain host-specific.

To update an installation:

1. Update canonical Reading Pack data through the normal reviewed workflow.
2. Run the ordinary `build` and `check` commands.
3. Rebuild and check the Agent Skill.
4. Replace the host's installed copy using that host's documented update method.

Do not edit the installed references as an update mechanism. Those changes are neither canonical nor durable and will fail `agent-skill check` when copied back.

## What a generic host does not guarantee

The container preserves the reviewed Reading Pack bytes and supplies generic usage instructions. A generic Agent Skills host does not thereby guarantee that it will enforce release gates, recheck freshness, retain an audit trail, obey every `SYS` rule, expose web access safely, or refuse access to unrelated files. Host behavior and permissions must be evaluated separately.

A dedicated `reading-pack-bot` can add a controlled upload flow, version policy, validation results, reader UI, and product-specific enforcement. This generic Agent Skill distribution does none of those things. It is the portable compatibility route when a host already knows how to load Agent Skills.
