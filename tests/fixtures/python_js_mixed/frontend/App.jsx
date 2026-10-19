import { Panel } from "./components";
import { formatUser } from "./utils.js";
import { apiClient } from "@/api/client";

/** Root view: shows the signed-in user inside a panel. */
export function App({ user }) {
  return <Panel title={formatUser(user)} client={apiClient} />;
}
