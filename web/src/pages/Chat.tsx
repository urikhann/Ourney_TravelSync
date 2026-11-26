// Trip chat: the group talks here, and TravelSync (the AI agent) answers when someone asks it.
// Only messages that start with "@TravelSync" (short: "@ai" or "@ts") or come from a quick-action chip go to the AI.
// Tapping the @ button (or typing "@ai ") turns on "ask TravelSync" mode: a pill shows in the box and the
// message is sent as "@TravelSync …". Backspace on an empty box turns it off again.
// The screen polls every few seconds; the backend keeps the history (GET/POST /trips/{id}/chat).
import { FormEvent, KeyboardEvent, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useIsMutating, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AtSign, Check, CloudRain, Footprints, Lightbulb, SendHorizontal, Sparkles, AlertTriangle, X } from "lucide-react";
import { api, message } from "../api";
import { useMe } from "../auth";
import type { AgentProposal, ChatMessage, ChatPage, Place, ProposalStatus, Trip } from "../types";
import { ErrorBox, Loading } from "../ui";
import TopBar from "../components/TopBar";
import TabBar from "../components/TabBar";
import PlaceSheet from "../components/PlaceSheet";
import { Avatar } from "../components/Avatar";
import { locale, t as tr } from "../i18n";
import "./chat.css";

const AGENT = "TravelSync";
const MENTION = /^\s*@(travelsync|ai|ts)(\s|$)/i;
const QUICK = [
  { Icon: CloudRain, label: "It's raining", text: "It's raining — can we swap outdoor stops for indoor places?" },
  { Icon: Footprints, label: "Less walking", text: "We're tired — please make today's plan need less walking." },
  { Icon: Lightbulb, label: "Why this plan?", text: "Why were these places picked for our group?" },
];
const time = (iso: string) => new Date(iso).toLocaleTimeString(locale(), { hour: "2-digit", minute: "2-digit" });

export default function Chat() {
  const { id = "" } = useParams();
  const qc = useQueryClient();
  const activeSends = useIsMutating({ mutationKey: ["chat-send", id] });
  const trip = useQuery({ queryKey: ["trip", id], queryFn: () => api.get<Trip>(`/trips/${id}`) });
  const chat = useQuery({
    queryKey: ["chat", id], queryFn: () => api.get<ChatPage>(`/trips/${id}/chat`),
    refetchInterval: 3000, // simple polling; WebSockets can replace this later without changing the screen
  });
  const [text, setText] = useState("");
  const [askAI, setAskAI] = useState(false);  // "ask TravelSync" mode (pill in the box)
  const inputRef = useRef<HTMLInputElement>(null);
  const submitting = useRef(false); // closes the gap before React renders pending state
  const [pending, setPending] = useState<{ text: string; afterId: number } | null>(null);
  const bottom = useRef<HTMLDivElement>(null);

  const send = useMutation({
    mutationKey: ["chat-send", id],
    mutationFn: (b: { text: string; trigger: "MESSAGE" | "MENTION" | "QUICK_ACTION" }) =>
      api.post<{ messages: ChatMessage[] }>(`/trips/${id}/chat`, b),
    onMutate: (b) => {
      if (b.trigger === "MESSAGE") return;
      const messages = qc.getQueryData<ChatPage>(["chat", id])?.messages ?? [];
      setPending({ text: b.text.trim().replace(MENTION, `@${AGENT}$2`), afterId: messages[messages.length - 1]?.id ?? 0 });
    },
    onSettled: async () => {
      try {
        await qc.invalidateQueries({ queryKey: ["chat", id] });
      } finally {
        setPending(null);
        submitting.current = false;
      }
    },
  });
  const busy = send.isPending || activeSends > 0;

  const msgs = chat.data?.messages ?? [];
  useEffect(() => { bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [msgs.length, pending]);

  const me = useMe().data?.id ?? trip.data?.members.find((m) => m.is_me)?.user_id;
  // Polling can receive the saved question while the agent is still replying.
  const pendingSaved = pending && me && msgs.some((m) =>
    m.id > pending.afterId && m.kind === "user" && m.author?.id === me && m.text === pending.text);
  const colorIndex = useMemo(() => Object.fromEntries((trip.data?.members ?? []).map((m, i) => [m.user_id, i])), [trip.data]);

  const submit = (e?: FormEvent) => {
    e?.preventDefault();
    const t = text.trim();
    if (!t || busy || submitting.current) return;
    const toAI = askAI || MENTION.test(t);
    const body = askAI ? `@${AGENT} ${t}` : t;
    const draft = { text, askAI };
    const afterId = msgs[msgs.length - 1]?.id ?? 0;
    submitting.current = true;
    setText("");
    setAskAI(false);
    send.mutate({ text: body, trigger: toAI ? "MENTION" : "MESSAGE" }, { onError: () => {
      // A lost response can follow a successful save. Restore only an unsaved
      // draft, after onSettled has refreshed chat, rather than inviting a resend.
      const savedText = body.replace(MENTION, `@${AGENT}$2`);
      const saved = qc.getQueryData<ChatPage>(["chat", id])?.messages.some(m =>
        m.id > afterId && m.kind === "user" && m.author?.id === me && m.text === savedText);
      if (!saved) { setText(draft.text); setAskAI(draft.askAI); }
    } });
  };
  const ask = (q: string) => {
    if (busy || submitting.current) return;
    submitting.current = true;
    send.mutate({ text: `@${AGENT} ${tr(q)}`, trigger: "QUICK_ACTION" });
  };
  const mention = () => { setAskAI((on) => !on); inputRef.current?.focus(); };
  const type = (v: string) => {
    const m = v.match(MENTION);
    if (m && !askAI) { setAskAI(true); setText(v.slice(m[0].length)); return; }  // "@ai " becomes the pill
    setText(v);
  };
  const keys = (e: KeyboardEvent<HTMLInputElement>) => { if (e.key === "Backspace" && askAI && !text) setAskAI(false); };
  const asking = askAI || MENTION.test(text);
  const suggest = !askAI && /^@\w{0,9}$/i.test(text) && !MENTION.test(text);  // typed "@": offer TravelSync

  return (
    <>
      <TopBar title={trip.data ? trip.data.name : tr("Trip chat")} back={`/trips/${id}`} />
      <main className="content chat">
        {chat.isLoading ? <Loading /> : chat.isError ? <ErrorBox error={message(chat.error)} /> : (
          <div className="chat-list">
            <AgentIntro />
            {msgs.map((m, i) => {
              const prev = msgs[i - 1];
              const grouped = prev && prev.kind === m.kind && prev.author?.id === m.author?.id;
              return m.kind === "agent"
                ? <AgentBubble key={m.id} m={m} tripId={id} />
                : <UserBubble key={m.id} m={m} mine={m.author?.id === me} grouped={!!grouped} color={colorIndex[m.author?.id ?? ""] ?? 0} />;
            })}
            {pending && (
              <>
                {!pendingSaved && <UserBubble mine grouped={false} color={0}
                  m={{ id: -1, kind: "user", text: pending.text, reply_to: null, created_at: new Date().toISOString(), author: { id: me ?? null, name: "" }, agent: null }} />}
                <Thinking question={pending.text} />
              </>
            )}
            <div ref={bottom} className="chat-end" />
          </div>
        )}
      </main>

      <div className="chat-dock">
        <div className="chat-quick" role="group" aria-label={tr("Ask {name}", { name: AGENT })}>
          {QUICK.map(({ Icon, label, text: q }) => (
            <button key={label} type="button" className="chat-chip" disabled={busy} onClick={() => ask(q)}>
              <Icon size={14} /> {tr(label)}
            </button>
          ))}
        </div>
        {suggest && !busy && (
          <button type="button" className="chat-suggest" onClick={() => { setText(""); setAskAI(true); inputRef.current?.focus(); }}>
            <span className="agent-av"><Sparkles size={13} /></span><b>@ai</b> <span className="muted">{tr("Ask {name}", { name: AGENT })}</span>
          </button>
        )}
        <ErrorBox error={send.error ? message(send.error) : null} />
        <form className={"chat-input" + (asking ? " asking" : "")} onSubmit={submit} aria-busy={busy}>
          <button type="button" className={"chat-at" + (askAI ? " on" : "")} aria-pressed={askAI}
            disabled={busy}
            aria-label={tr("Ask {name}", { name: AGENT })} title={tr("Ask {name}", { name: AGENT })} onClick={mention}>
            <AtSign size={17} />
          </button>
          {askAI && <button type="button" className="mention-pill" disabled={busy} onClick={() => setAskAI(false)} aria-label={tr("Stop asking {name}", { name: AGENT })}>
            <Sparkles size={12} /> {AGENT} <X size={12} /></button>}
          <input ref={inputRef} value={text} maxLength={2000} onChange={(e) => type(e.target.value)} onKeyDown={keys}
            disabled={busy}
            placeholder={busy ? tr("Waiting for the reply…") : askAI ? tr("Ask anything about the trip…") : tr("Message the group")} aria-label={tr("Message")} />
          <button className="chat-send" type="submit" aria-label={tr("Send")} disabled={!text.trim() || busy}>
            <SendHorizontal size={17} />
          </button>
        </form>
        <div className="chat-hint">{busy ? tr("Please wait for the reply.") : asking
          ? <><Sparkles size={12} /> {tr("{name} will read this message and the last few before it.", { name: AGENT })}</>
          : <><AtSign size={12} /> {tr("Tap @ or start with @ai to ask {name}. Other messages stay between you.", { name: AGENT })}</>}</div>
      </div>
      <TabBar tripId={id} />
    </>
  );
}

function AgentIntro() {
  return (
    <div className="chat-intro">
      <span className="agent-av lg"><Sparkles size={20} /></span>
      <div className="b">{tr("Plan together here")}</div>
      <p className="muted small">{tr("Chat with your group. Tap @ (or type @ai) or a chip below to ask the AI. Normal messages stay between you.")}</p>
    </div>
  );
}

function UserBubble({ m, mine, grouped, color }: { m: ChatMessage; mine: boolean; grouped: boolean; color: number }) {
  const parts = m.text.match(MENTION);
  const body = parts ? m.text.slice(parts[0].length) : m.text;
  return (
    <div className={"msg" + (mine ? " mine" : "") + (grouped ? " grouped" : "")}>
      {!mine && <span className="msg-av">{!grouped && <Avatar name={m.author?.name ?? "?"} i={color} size={28} />}</span>}
      <div className="msg-col">
        {!mine && !grouped && <div className="msg-name">{m.author?.name}</div>}
        <div className="bubble">{parts && <span className="mention">@{AGENT}</span>}{parts ? " " : ""}{body}</div>
        <div className="msg-time">{time(m.created_at)}</div>
      </div>
    </div>
  );
}

function AgentBubble({ m, tripId }: { m: ChatMessage; tripId: string }) {
  const a = m.agent;
  const failed = a?.state === "FAILED";
  const proposal = a?.proposal;
  const stopChange = proposal && typeof proposal === "object" && "date" in proposal &&
    typeof proposal.date === "string" && Array.isArray(proposal.remove_item_ids) && Array.isArray(proposal.add)
    ? proposal : null;
  return (
    <div className="msg agent">
      <span className="msg-av"><span className="agent-av"><Sparkles size={15} /></span></span>
      <div className="msg-col">
        <div className="msg-name">{AGENT} <span className="ai-tag">AI</span></div>
        <div className={"bubble" + (failed ? " failed" : "")}>
          {agentText(m) || tr("{name} can't answer right now. Please try again in a moment.", { name: AGENT })}
          {a?.state === "NEEDS_CLARIFICATION" && <div className="agent-note">{tr("Reply with @ai to answer.")}</div>}
          {failed && a?.error && <div className="agent-note">{tr("Error: {code}", { code: a.error })}</div>}
        </div>
        {!!a?.tool_calls.length && (
          <div className="tools">
            {a.tool_calls.map((t, i) => <span key={i} className="tool">{["gemini_error", "provider_error"].includes(t.name) ? <AlertTriangle size={12} /> : <Check size={12} />} {toolText(t)}</span>)}
          </div>
        )}
        {stopChange && <ProposalCard m={m} proposal={stopChange} tripId={tripId} />}
        {a?.pending_action?.status === "PENDING" && <ConfirmationCard m={m} tripId={tripId} />}
        <div className="msg-time">{time(m.created_at)}</div>
      </div>
    </div>
  );
}

/** Group-vote result notes are written by the server in English; show them in the reader's language. */
function agentText(m: ChatMessage): string {
  const r = m.agent?.result;
  if (m.agent?.event !== "proposal_result" || !r || r.approve == null) return m.text;
  if (r.status === "APPLIED") {
    if (m.agent?.action_type && m.agent.action_type !== "replace_poi") return m.text;
    const when = r.start ? tr(" at {time}", { time: r.start }) : "";
    return tr("✅ {a} of {n} approved, so I updated the plan: {names} is now on {date}{when}.", {
      a: r.approve, n: r.members ?? 0, names: (r.added ?? []).join(", "), date: r.date ?? "", when }) + " "
      + (r.mode === "keep" ? tr("The other stops stay, though their times may shift a little to fit the new travel times.")
        : tr("To fit it in, I rebuilt the day, so some other stops changed too.")) + " " + tr("Check the Plan tab.");
  }
  if (r.status === "REJECTED") return tr("The group rejected this suggestion, so the plan stays the same.");
  if (r.status === "STALE") return tr("This suggestion is out of date because the plan changed. Ask me again for a new one.");
  if (m.text) return m.text;
  return tr("The group approved, but I couldn't fit this change into a valid plan ({reason}). The plan is unchanged.", { reason: r.reason ?? "" });
}

function ConfirmationCard({ m, tripId }: { m: ChatMessage; tripId: string }) {
  const qc = useQueryClient();
  const activeSends = useIsMutating({ mutationKey: ["chat-send", tripId] });
  const me = useMe();
  const confirm = useMutation({
    mutationKey: ["chat-send", tripId],
    mutationFn: (yes: boolean) => api.post(`/trips/${tripId}/chat`, { text: `@TravelSync ${tr(yes ? "Yes" : "No")}`, reply_to: m.id }),
    onSettled: () => qc.invalidateQueries({ queryKey: ["chat", tripId] }),
  });
  if (me.data?.id !== m.agent?.pending_action?.authorized_user_id) return null;
  const respond = (yes: boolean) => {
    if (qc.isMutating({ mutationKey: ["chat-send", tripId] })) return;
    confirm.mutate(yes);
  };
  return <div className="proposal-actions">
    <button className="btn sm" disabled={confirm.isPending || activeSends > 0} onClick={() => respond(true)}>{tr("Confirm search or replanning")}</button>
    <button className="btn ghost sm" disabled={confirm.isPending || activeSends > 0} onClick={() => respond(false)}>{tr("Cancel")}</button>
    {confirm.error && <span className="tiny proposal-error">{message(confirm.error)}</span>}
  </div>;
}

function toolText(tc: { name: string; summary: string }): string {
  const vote = tc.summary.match(/^Group vote: (\d+) approve, (\d+) reject$/);
  if (vote) return tr("Group vote: {a} approve, {r} reject", { a: vote[1], r: vote[2] });
  return tr(tc.summary || tc.name);
}

const CLOSED: Record<Exclude<ProposalStatus, "PENDING">, string> = {
  APPLYING: "Approved — updating the plan…",
  APPLIED: "Approved — the plan was updated.",
  REJECTED: "Rejected by the group — the plan stays the same.",
  FAILED: "Approved, but it didn't fit into a valid plan. The plan is unchanged.",
  STALE: "Out of date — the plan changed after this suggestion.",
};

/** A suggested change to the plan. It only changes the plan after a majority of the group approves (issue #19). */
function ProposalCard({ m, proposal: p, tripId }: { m: ChatMessage; proposal: AgentProposal; tripId: string }) {
  const a = m.agent!;
  const v = a.votes;
  const status: ProposalStatus = a.proposal_status ?? "STALE";
  const qc = useQueryClient();
  const vote = useMutation({
    mutationFn: (approve: boolean) => api.post(`/trips/${tripId}/chat/${m.id}/vote`, { approve }),
    onSettled: () => {  // the deciding vote can rebuild the plan, so refresh both screens
      qc.invalidateQueries({ queryKey: ["chat", tripId] });
      qc.invalidateQueries({ queryKey: ["plans", tripId] });
    },
  });
  const removed = a.removed ?? [];
  const scopeLabels: Record<string, string> = { replace_poi: "Replace an activity", remove_poi: "Remove an activity", add_poi: "Add an activity",
    replan_day: "Replan one date", replan_unlocked: "Replan unlocked activities", replan_all: "Replan the whole trip" };
  const multipleDates = (a.affected_dates?.length ?? 0) > 1;
  return (
    <div className={"proposal " + status.toLowerCase()}>
      <div className="eyebrow" style={{ color: "var(--purple)" }}>{tr("Suggested change · {date}", { date: p.date })}</div>
      {a.action_type && <div className="small muted">{tr(scopeLabels[a.action_type] ?? "Suggested change")} · {(a.affected_dates ?? [p.date]).join(", ")}</div>}
      {removed.map((r, index) => <AddedStop key={r.poi_id} poiId={r.poi_id} start={r.start}
        date={p.remove_item_ids[index]?.split("@")[0] ?? p.date} version={a.dataset_version ?? undefined} removed showDate={multipleDates} fallbackName={r.name} />)}
      {!removed.length && p.remove_item_ids.length > 0 &&
        <div className="small muted">{p.remove_item_ids.length > 1 ? tr("Remove {n} stops", { n: p.remove_item_ids.length }) : tr("Remove 1 stop")}</div>}
      {p.add.map((x) => <div key={x.poi_id + x.start}>{multipleDates && <span className="tiny muted">{x.date ?? p.date}</span>}<AddedStop poiId={x.poi_id} start={x.start} date={x.date ?? p.date} version={a.dataset_version ?? undefined} /></div>)}
      {a.validation_status === "PASSED" && <div className="tiny muted">{tr("Schedule validated before voting")}</div>}
      {a.affected_activities?.map((x) => <div key={x.date + x.poi_id} className="small muted">
        {tr("Adjusted activity time")}: {x.date} <AddedStop poiId={x.poi_id} date={x.date} version={a.dataset_version ?? undefined}
          start={`${Math.floor(x.start_min / 60).toString().padStart(2, "0")}:${(x.start_min % 60).toString().padStart(2, "0")}`} />
      </div>)}
      {status === "PENDING" ? (
        <>
          <div className="proposal-actions">
            <button className={"btn sm" + (v?.mine === true ? " green" : "")} disabled={vote.isPending}
              aria-pressed={v?.mine === true} onClick={() => vote.mutate(true)}><Check size={15} /> {tr("Approve")}</button>
            <button className={"btn ghost sm" + (v?.mine === false ? " picked" : "")} disabled={vote.isPending}
              aria-pressed={v?.mine === false} onClick={() => vote.mutate(false)}><X size={15} /> {tr("Reject")}</button>
          </div>
          <div className="tiny muted">
            {vote.isPending ? tr("Saving your vote and checking the approved schedule…")
              : v ? tr("{a} approve · {r} reject · {need} of {n} approvals needed", { a: v.approve, r: v.reject, need: v.needed, n: v.members })
              + (v.mine === null ? "" : " · " + (v.mine ? tr("you approved") : tr("you rejected"))) : ""}
          </div>
        </>
      ) : (
        <div className={"proposal-status " + status.toLowerCase()}>
          {tr(CLOSED[status])}
          {status === "APPLIED" && <> <Link to={`/trips/${tripId}/plan`}>{tr("Open the plan")}</Link></>}
          {v && status !== "STALE" && <span className="muted"> ({tr("{a} approve · {r} reject", { a: v.approve, r: v.reject })})</span>}
        </div>
      )}
      {vote.error && <div className="tiny proposal-error">{message(vote.error)}</div>}
    </div>
  );
}

function AddedStop({ poiId, start, date, version, removed, showDate, fallbackName }: {
  poiId: string; start: string; date: string; version?: string; removed?: boolean; showDate?: boolean; fallbackName?: string;
}) {
  const [open, setOpen] = useState(false);
  const place = useQuery({ queryKey: ["poi", poiId, version], queryFn: () => api.get<Place>(
    `/pois/${encodeURIComponent(poiId)}${version ? `?version=${encodeURIComponent(version)}` : ""}`), staleTime: 60_000 });
  const name = place.data?.name ?? fallbackName ?? poiId;
  return <div className={removed ? "removed" : "added"}>
    {showDate && <div className="tiny muted">{date}</div>}
    <span className="num">{start}</span>{" "}
    <button type="button" className="proposal-place" onClick={() => setOpen(true)} aria-label={tr("View details for {place}", { place: name })}>
      {removed ? <s>{name}</s> : <b>{name}</b>} · {tr("View place details")}
    </button>
    <div className="muted tiny">{place.data?.category || tr("Category not available")}{place.data?.area ? ` · ${place.data.area}` : ""}</div>
    <PlaceSheet poiId={open ? poiId : null} onClose={() => setOpen(false)} dates={[date]} start={start} version={version} />
  </div>;
}

function Thinking({ question }: { question: string }) {
  return (
    <div className="msg agent">
      <span className="msg-av"><span className="agent-av"><Sparkles size={15} /></span></span>
      <div className="msg-col">
        <div className="msg-name">{AGENT} <span className="ai-tag">AI</span></div>
        <div className="bubble thinking" aria-live="polite" title={question}>
          <span className="dots"><i /><i /><i /></span> {tr("Thinking…")}
        </div>
      </div>
    </div>
  );
}
