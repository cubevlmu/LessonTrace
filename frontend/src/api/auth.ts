import { z } from "zod";
import { accountSchema } from "../models/backend";
import { request } from "./client";
export const me = () => request("me", accountSchema);
export const login = (identifier: string, password?: string) => {
  const data = new FormData();
  if (password === undefined) data.append("token", identifier);
  else { data.append("username", identifier); data.append("password", password); }
  return request("login", accountSchema, data);
};
export const logout = () =>
  request("logout", z.object({ logged_out: z.boolean() }), {}, "POST");
