import { ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { ChevronLeft } from "lucide-react";

/** The bar at the top of every screen: optional back button, centered title, optional right button. */
export default function TopBar({ title, back, right }: { title: string; back?: string; right?: ReactNode }) {
  const nav = useNavigate();
  return (
    <header className="topbar">
      {back ? <button className="iconbtn" aria-label="Back" onClick={() => nav(back)}><ChevronLeft size={20} /></button> : <span className="spacer" />}
      <h1>{title}</h1>
      {right ?? <span className="spacer" />}
    </header>
  );
}
