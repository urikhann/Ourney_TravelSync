// web/src/password.ts  (NEW file)
// Shared password helpers for Signup and Forgot password.

/** How strong is the password? 0 = too short, 1 = okay, 2 = good, 3 = strong. Only "8+ characters" is required. */
export function strength(pw: string): 0 | 1 | 2 | 3 {
  if (pw.length < 8) return 0;
  let s = 1;
  if (/[A-Za-z]/.test(pw) && /\d/.test(pw)) s++;
  if (pw.length >= 12 || /[^A-Za-z0-9]/.test(pw)) s++;
  return s as 1 | 2 | 3;
}

export const STRENGTH = ["Too short", "Okay", "Good", "Strong"];
