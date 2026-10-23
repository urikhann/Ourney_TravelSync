import { InputHTMLAttributes, useState } from "react";
import { Eye, EyeOff, Lock } from "lucide-react";

/** A password box with a lock icon and a show / hide button. Pass the usual <input> props. */
export default function PasswordInput(props: InputHTMLAttributes<HTMLInputElement>) {
  const [show, setShow] = useState(false);
  return (
    <div className="inp">
      <Lock size={17} className="inp-ico" aria-hidden="true" />
      <input {...props} type={show ? "text" : "password"} />
      <button type="button" className="inp-eye" onClick={() => setShow(!show)}
        aria-label={show ? "Hide password" : "Show password"} aria-pressed={show}>
        {show ? <EyeOff size={17} /> : <Eye size={17} />}
      </button>
    </div>
  );
}
