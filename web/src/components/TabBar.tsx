import { NavLink } from "react-router-dom";
import { House, Map, Sparkles, User } from "lucide-react";

/** Bottom tabs inside a trip. NavLink knows which tab is active from the URL. */
export default function TabBar({ tripId }: { tripId: string }) {
  const tabs = [
    { to: `/trips/${tripId}`, label: "Trip", Icon: House, end: true },
    { to: `/trips/${tripId}/places`, label: "Places", Icon: Sparkles, end: false },
    { to: `/trips/${tripId}/plan`, label: "Plan", Icon: Map, end: false },
    { to: `/trips/${tripId}/preferences`, label: "Me", Icon: User, end: false },
  ];
  return (
    <nav className="tabbar" aria-label="Trip sections">
      {tabs.map(({ to, label, Icon, end }) => (
        <NavLink key={to} to={to} end={end} className={({ isActive }) => "tab" + (isActive ? " on" : "")}>
          <Icon size={22} />{label}
        </NavLink>
      ))}
    </nav>
  );
}
