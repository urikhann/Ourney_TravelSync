// Trip chat: the group talks here, and TravelSync (the AI agent) answers when someone asks it.
// Only messages that start with "@TravelSync" or come from a quick-action chip go to the AI.
// The screen polls every few seconds; the backend keeps the history (GET/POST /trips/{id}/chat).
import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AtSign, Check, CloudRain, Footprints, Lightbulb, SendHorizontal, Sparkles, AlertTriangle, X } from "lucide-react";
import { api, message } from "../api";
import type { ChatMessage, ChatPage, Place, ProposalStatus, Trip } from "../types";
import { ErrorBox, Loading } from "../ui";
import TopBar from "../components/TopBar";
import TabBar from "../components/TabBar";
import { Avatar } from "../components/Avatar";
import "./chat.css";

const AGENT = "TravelSync";
const MENTION = /^\s*@travelsync(\s|$)/i;
const QUICK = [
  { Icon: CloudRain, label: "It's raining", text: "It's raining — can we swap outdoor stops for indoor places?" },
  { Icon: Footprints, label: "Less walking", text: "We're tired — please make today's plan need less walking." },
  { Icon: Lightbulb, label: "Why this plan?", text: "Why were these places picked for our group?" },
];
const time = (iso: string) => new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

export default function Chat() {
  const { id = "" } = useParams();
  const qc = useQueryClient();
  const trip = useQuery({ queryKey: ["trip", id], queryFn: () => api.get<Trip>(`/trips/${id}`) });
  const chat = useQuery({
    queryKey: ["chat", id], queryFn: () => api.get<ChatPage>(`/trips/${id}/chat`),
    refetchInterval: 3000, // simple polling; WebSockets can replace this later without changing the screen
  });
  const [text, setText] = useState("");
  const [pending, setPending] = useState<string | null>(null); // the question TravelSync is answering right now
  const bottom = useRef<HTMLDivElement>(null);

  const send = useMutation({
    mutationFn: (b: { text: string; trigger: "MESSAGE" | "MENTION" | "QUICK_ACTION" }) =>
      api.post<{ messages: ChatMessage[] }>(`/trips/${id}/chat`, b),
    onMutate: (b) => { if (b.trigger !== "MESSAGE") setPending(b.text); },
    onSettled: () => { setPending(null); qc.invalidateQueries({ queryKey: ["chat", id] }); },
  });

  const msgs = chat.data?.messages ?? [];
  useEffect(() => { bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [msgs.length, pending]);

  const me = trip.data?.members.find((m) => m.is_me)?.user_id;
  const colorIndex = useMemo(() => Object.fromEntries((trip.data?.members ?? []).map((m, i) => [m.user_id, i])), [trip.data]);

  const submit = (e?: FormEvent) => {
    e?.preventDefault();
    const t = text.trim();
    if (!t || send.isPending) return;
    send.mutate({ text: t, trigger: MENTION.test(t) ? "MENTION" : "MESSAGE" }, { onSuccess: () => setText("") });
  };
  const ask = (q: string) => send.mutate({ text: `@${AGENT} ${q}`, trigger: "QUICK_ACTION" });
  const mention = () => setText((t) => (MENTION.test(t) ? t : `@${AGENT} ${t}`));
  const asking = MENTION.test(text);

  return (
    <>
      <TopBar title={trip.data ? trip.data.name : "Trip chat"} back={`/trips/${id}`} />
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
            {pending && (  // show my question right away while TravelSync works on it
              <>
                <UserBubble mine grouped={false} color={0}
                  m={{ id: -1, kind: "user", text: pending, reply_to: null, created_at: new Date().toISOString(), author: { id: me ?? null, name: "" }, agent: null }} />
                <Thinking question={pending} />
              </>
            )}
            <div ref={bottom} className="chat-end" />
          </div>
        )}
      </main>

      <div className="chat-dock">
        <div className="chat-quick" role="group" aria-label={`Ask ${AGENT}`}>
          {QUICK.map(({ Icon, label, text: q }) => (
            <button key={label} type="button" className="chat-chip" disabled={send.isPending} onClick={() => ask(q)}>
              <Icon size={14} /> {label}
            </button>
          ))}
        </div>
        <ErrorBox error={send.error ? message(send.error) : null} />
        <form className={"chat-input" + (asking ? " asking" : "")} onSubmit={submit}>
          <button type="button" className="chat-at" aria-label={`Ask ${AGENT}`} title={`Ask ${AGENT}`} onClick={mention}>
            <AtSign size={17} />
          </button>
          <input value={text} maxLength={2000} onChange={(e) => setText(e.target.value)}
            placeholder={`Message the group, or @${AGENT} to ask`} aria-label="Message" />
          <button className="chat-send" type="submit" aria-label="Send" disabled={!text.trim() || send.isPending}>
            <SendHorizontal size={17} />
          </button>
        </form>
        {asking && <div className="chat-hint"><Sparkles size={12} /> {AGENT} will read this message and the last few before it.</div>}
      </div>
      <TabBar tripId={id} />
    </>
  );
}

function AgentIntro() {
  return (
    <div className="chat-intro">
      <span className="agent-av lg"><Sparkles size={20} /></span>
      <div className="b">Plan together here</div>
      <p className="muted small">Chat with your group. Start a message with <b>@{AGENT}</b> or tap a chip below to ask the AI.
        Normal messages stay between you.</p>
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
  return (
    <div className="msg agent">
      <span className="msg-av"><span className="agent-av"><Sparkles size={15} /></span></span>
      <div className="msg-col">
        <div className="msg-name">{AGENT} <span className="ai-tag">AI</span></div>
        <div className={"bubble" + (failed ? " failed" : "")}>
          {m.text}
          {a?.state === "NEEDS_CLARIFICATION" && <div className="agent-note">Reply with @{AGENT} to answer.</div>}
          {failed && a?.error && <div className="agent-note">Error: {a.error}</div>}
        </div>
        {!!a?.tool_calls.length && (
          <div className="tools">
            {a.tool_calls.map((t, i) => <span key={i} className="tool">{["gemini_error", "provider_error"].includes(t.name) ? <AlertTriangle size={12} /> : <Check size={12} />} {t.summary || t.name}</span>)}
          </div>
        )}
        {a?.proposal && <ProposalCard m={m} tripId={tripId} />}
        <div className="msg-time">{time(m.created_at)}</div>
      </div>
    </div>
  );
}

const CLOSED: Record<Exclude<ProposalStatus, "PENDING">, string> = {
  APPLYING: "Approved — updating the plan…",
  APPLIED: "Approved — the plan was updated.",
  REJECTED: "Rejected by the group — the plan stays the same.",
  FAILED: "Approved, but it didn't fit into a valid plan. The plan is unchanged.",
  STALE: "Out of date — the plan changed after this suggestion.",
};

/** A suggested change to the plan. It only changes the plan after a majority of the group approves (issue #19). */
function ProposalCard({ m, tripId }: { m: ChatMessage; tripId: string }) {
  const a = m.agent!;
  const p = a.proposal!;
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
  return (
    <div className={"proposal " + status.toLowerCase()}>
      <div className="eyebrow" style={{ color: "var(--purple)" }}>Suggested change · {p.date}</div>
      {removed.map((r) => <div key={r.poi_id} className="removed"><span className="num">{r.start}</span> <s>{r.name}</s></div>)}
      {!removed.length && p.remove_item_ids.length > 0 &&
        <div className="small muted">Remove {p.remove_item_ids.length} stop{p.remove_item_ids.length > 1 ? "s" : ""}</div>}
      {p.add.map((x) => <AddedStop key={x.poi_id + x.start} poiId={x.poi_id} start={x.start} />)}
      {status === "PENDING" ? (
        <>
          <div className="proposal-actions">
            <button className={"btn sm" + (v?.mine === true ? " green" : "")} disabled={vote.isPending}
              aria-pressed={v?.mine === true} onClick={() => vote.mutate(true)}><Check size={15} /> Approve</button>
            <button className={"btn ghost sm" + (v?.mine === false ? " picked" : "")} disabled={vote.isPending}
              aria-pressed={v?.mine === false} onClick={() => vote.mutate(false)}><X size={15} /> Reject</button>
          </div>
          <div className="tiny muted">
            {vote.isPending ? "Saving your vote… if it's the deciding one, the plan is rebuilt (up to a minute)."
              : v ? `${v.approve} approve · ${v.reject} reject · ${v.needed} of ${v.members} approvals needed`
              + (v.mine === null ? "" : ` · you ${v.mine ? "approved" : "rejected"}`) : ""}
          </div>
        </>
      ) : (
        <div className={"proposal-status " + status.toLowerCase()}>
          {CLOSED[status]}
          {status === "APPLIED" && <> <Link to={`/trips/${tripId}/plan`}>Open the plan</Link></>}
          {v && status !== "STALE" && <span className="muted"> ({v.approve} approve · {v.reject} reject)</span>}
        </div>
      )}
      {vote.error && <div className="tiny proposal-error">{message(vote.error)}</div>}
    </div>
  );
}

function AddedStop({ poiId, start }: { poiId: string; start: string }) {
  const place = useQuery({ queryKey: ["poi", poiId], queryFn: () => api.get<Place>(`/pois/${poiId}`), staleTime: 60_000 });
  return <div className="added"><span className="num">{start}</span> <b>{place.data?.name ?? poiId}</b> <span className="muted tiny">{place.data?.area}</span></div>;
}

function Thinking({ question }: { question: string }) {
  return (
    <div className="msg agent">
      <span className="msg-av"><span className="agent-av"><Sparkles size={15} /></span></span>
      <div className="msg-col">
        <div className="msg-name">{AGENT} <span className="ai-tag">AI</span></div>
        <div className="bubble thinking" aria-live="polite" title={question}>
          <span className="dots"><i /><i /><i /></span> Thinking…
        </div>
      </div>
    </div>
  );
}
