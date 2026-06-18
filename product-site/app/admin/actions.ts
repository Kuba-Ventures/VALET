"use server";

import { redirect } from "next/navigation";
import {
  passwordMatches,
  setAdminSession,
  clearAdminSession,
} from "@/lib/admin-auth";

/**
 * Login action for the /admin password gate. On the right password we set the
 * session cookie and reload; on a wrong one we bounce back with ?error=1 (we
 * never echo the attempt). The form posts here directly.
 */
export async function login(formData: FormData) {
  const password = String(formData.get("password") ?? "");
  if (!passwordMatches(password)) {
    redirect("/admin?error=1");
  }
  await setAdminSession();
  redirect("/admin");
}

export async function logout() {
  await clearAdminSession();
  redirect("/admin");
}
