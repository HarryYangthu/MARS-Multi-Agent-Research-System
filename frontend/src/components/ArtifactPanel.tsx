"use client";

import { useCallback, useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import {
  approveArtifact,
  editArtifact,
  getArtifact,
  getDebateTranscript,
  listVersions,
  rejectArtifact,
  STAGE_TO_STEM,
  type ArtifactView,
  type Stage,
} from "@/lib/api";
import { useI18n } from "@/lib/i18n";

type Props = {
  runId: string;
  stage: Stage;
  state: string;
  onChanged?: () => void;
};

/** Pick the version to show. Once the node is approved/done, show the
 * read-only "approved" copy; otherwise act on the highest draft (vN). */
function pickVersion(versions: { version: string }[], state: string): string | null {
  const hasApproved = versions.some((v) => v.version === "approved");
  if ((state === "approved" || state === "done") && hasApproved) return "approved";
  const drafts = versions
    .map((v) => v.version)
    .filter((v) => v.startsWith("v"))
    .sort((a, b) => Number(a.slice(1)) - Number(b.slice(1)));
  if (drafts.length) return drafts[drafts.length - 1];
  if (hasApproved) return "approved";
  return null;
}

export function ArtifactPanel({ runId, stage, state, onChanged }: Props): JSX.Element {
  const { t } = useI18n();
  const stem = STAGE_TO_STEM[stage];
  const [art, setArt] = useState<ArtifactView | null>(null);
  const [version, setVersion] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draftBody, setDraftBody] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [debate, setDebate] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const versions = await listVersions(runId, stage, stem);
      const v = pickVersion(versions, state);
      if (!v) {
        setArt(null);
        setVersion(null);
        return;
      }
      const a = await getArtifact(runId, stage, stem, v);
      setArt(a);
      setVersion(v);
      setDraftBody(bodyOf(a));
    } catch (e) {
      setArt(null);
      setErr(String(e));
    } finally {
      setLoading(false);
    }
  }, [runId, stage, stem, state]);

  useEffect(() => {
    setEditing(false);
    setDebate(null);
    void load();
    // Re-load when the node's state changes (e.g. draft just produced).
  }, [load, state]);

  const isApproved = version === "approved";
  const canAct = !!version && !isApproved;

  async function doApprove(): Promise<void> {
    if (!version) return;
    setBusy(true);
    setErr(null);
    try {
      await approveArtifact(runId, stage, stem, version);
      onChanged?.();
      await load();
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function doReject(): Promise<void> {
    const reason = window.prompt(t("artifact.reject_reason") ?? "Reason?");
    if (reason === null) return;
    setBusy(true);
    setErr(null);
    try {
      await rejectArtifact(runId, stage, stem, reason || "rejected");
      onChanged?.();
      await load();
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function doSaveEdit(): Promise<void> {
    if (!version) return;
    setBusy(true);
    setErr(null);
    try {
      const updated = await editArtifact(runId, stage, stem, version, { body: draftBody });
      setArt(updated);
      setEditing(false);
      onChanged?.();
      await load();
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function toggleDebate(): Promise<void> {
    if (debate !== null) {
      setDebate(null);
      return;
    }
    try {
      const d = await getDebateTranscript(runId, stage);
      setDebate(d.exists ? d.text : (t("debate.empty") ?? "—"));
    } catch (e) {
      setDebate(String(e));
    }
  }

  if (loading && !art) {
    return <p className="p-4 text-xs text-slate-500">{t("common.loading")}</p>;
  }

  if (!art) {
    return (
      <div className="p-4 text-xs text-slate-500">
        {t("artifact.none")}
        {err ? <p className="mt-2 text-rose-300">{err}</p> : null}
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      {/* header */}
      <div className="flex flex-wrap items-center gap-2 border-b border-mars-border px-3 py-2 text-[11px]">
        <span className="font-mono text-slate-300">{stem}</span>
        <span className="rounded bg-mars-subtle px-1.5 py-0.5 font-mono text-slate-400">
          {version}
        </span>
        {art.schema_id ? (
          <span className="rounded bg-mars-accent/20 px-1.5 py-0.5 font-mono text-mars-accent">
            {art.schema_id}
          </span>
        ) : null}
        {art.valid ? (
          <span className="rounded bg-emerald-500/20 px-1.5 py-0.5 text-emerald-300">
            ✓ {t("artifact.valid")}
          </span>
        ) : (
          <span className="rounded bg-rose-500/20 px-1.5 py-0.5 text-rose-300">
            ✗ {t("artifact.invalid")}
          </span>
        )}
        <span className="ml-auto" />
        <button onClick={() => void toggleDebate()} className="btn-ghost">
          {debate !== null ? t("debate.hide") : t("debate.show")}
        </button>
        <button onClick={() => void load()} className="btn-ghost">
          ↻ {t("common.refresh")}
        </button>
      </div>

      {/* validation errors */}
      {!art.valid && art.errors.length ? (
        <ul className="border-b border-rose-500/30 bg-rose-500/5 px-3 py-1.5 text-[10px] text-rose-300">
          {art.errors.map((e, i) => (
            <li key={i}>
              <span className="font-mono">{e.path || "/"}</span>: {e.message}
            </li>
          ))}
        </ul>
      ) : null}

      {/* body */}
      <div className="min-h-0 flex-1 overflow-auto px-3 py-2">
        {debate !== null ? (
          <pre className="whitespace-pre-wrap text-[11px] text-slate-300">{debate}</pre>
        ) : editing ? (
          <textarea
            value={draftBody}
            onChange={(e) => setDraftBody(e.target.value)}
            className="h-full min-h-[300px] w-full resize-none rounded border border-mars-border bg-mars-bg/60 p-2 font-mono text-[12px] text-slate-200 outline-none focus:border-mars-accent"
          />
        ) : (
          <article className="prose prose-invert max-w-none text-[13px] prose-headings:text-slate-100 prose-p:text-slate-300 prose-li:text-slate-300 prose-code:text-amber-300">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{bodyOf(art)}</ReactMarkdown>
          </article>
        )}
      </div>

      {err ? <p className="px-3 py-1 text-[11px] text-rose-300">{err}</p> : null}

      {/* actions */}
      <div className="flex items-center gap-2 border-t border-mars-border px-3 py-2 text-xs">
        {isApproved ? (
          <span className="text-emerald-300">✓ {t("state.approved")}</span>
        ) : editing ? (
          <>
            <button disabled={busy} onClick={() => void doSaveEdit()} className="btn-primary">
              💾 {t("artifact.save")}
            </button>
            <button onClick={() => setEditing(false)} className="btn-ghost">
              {t("artifact.cancel")}
            </button>
          </>
        ) : (
          <>
            <button
              disabled={busy || !canAct}
              onClick={() => void doApprove()}
              className="btn-primary disabled:opacity-40"
            >
              ✓ {t("run.approve")}
            </button>
            <button
              disabled={busy || !canAct}
              onClick={() => void doReject()}
              className="rounded bg-rose-500/80 px-3 py-1 text-white hover:bg-rose-500 disabled:opacity-40"
            >
              ✗ {t("run.reject")}
            </button>
            <button
              disabled={busy || !canAct}
              onClick={() => {
                setDraftBody(bodyOf(art));
                setEditing(true);
              }}
              className="btn-ghost disabled:opacity-40"
            >
              ✎ {t("artifact.edit")}
            </button>
          </>
        )}
      </div>

      <style jsx>{`
        .btn-primary {
          border-radius: 0.3rem;
          background: #6366f1;
          padding: 0.25rem 0.75rem;
          color: white;
        }
        .btn-primary:hover {
          background: #818cf8;
        }
        .btn-ghost {
          border-radius: 0.3rem;
          background: #1b1e26;
          padding: 0.2rem 0.6rem;
          color: #cbd5e1;
        }
        .btn-ghost:hover {
          background: #262a34;
        }
      `}</style>
    </div>
  );
}

/** Strip the leading YAML frontmatter block so we render/edit just the body. */
function bodyOf(a: ArtifactView): string {
  const m = a.text.match(/^\s*---\n[\s\S]*?\n---\n?/);
  return m ? a.text.slice(m[0].length).replace(/^\n+/, "") : a.text;
}
