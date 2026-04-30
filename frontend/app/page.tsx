import { redirect } from "next/navigation";
import { cookies } from "next/headers";

export default function HomePage() {
  const token = cookies().get("mcp_token")?.value;
  if (token) {
    redirect("/dashboard");
  } else {
    redirect("/login");
  }
}
