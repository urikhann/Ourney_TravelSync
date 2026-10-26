import { useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Calendar } from "lucide-react";
import { api, message } from "../api";
import type { Trip, TripSummary } from "../types";
import { ErrorBox, Loading, fmtDate } from "../ui";
import TopBar from "../components/TopBar";

export default function Join() {
  const { code = "" } = useParams();
  const nav = useNavigate();
  const qc = useQueryClient();
  const invite = useQuery({ queryKey: ["invite", code], queryFn: () => api.get<TripSummary & { already_member: boolean }>(`/invites/${code}`) });
  const join = useMutation({
    mutationFn: () => api.post<Trip>(`/trips/join/${code}`),
    onSuccess: (t) => { qc.invalidateQueries({ queryKey: ["trips"] }); nav(`/trips/${t.id}`, { replace: true }); },
  });

  return (
    <>
      <TopBar title="Trip invite" back="/trips" />
      <main className="content">
        {invite.isLoading ? <Loading /> : invite.error ? <ErrorBox error={message(invite.error)} /> : invite.data && (
          <div className="stack">
            <div className="hero">
              <div className="eyebrow" style={{ opacity: 0.7 }}>You're invited</div>
              <div className="name">{invite.data.name}</div>
              <div className="meta"><Calendar size={14} />{fmtDate(invite.data.start_date)} – {fmtDate(invite.data.end_date)} · {invite.data.member_count} going</div>
            </div>
            <ErrorBox error={join.error ? message(join.error) : null} />
            {invite.data.already_member
              ? <button className="btn" onClick={() => nav(`/trips/${invite.data.id}`)}>You're already in — open the trip</button>
              : <button className="btn blue" disabled={join.isPending} onClick={() => join.mutate()}>Join this trip</button>}
          </div>
        )}
      </main>
    </>
  );
}
