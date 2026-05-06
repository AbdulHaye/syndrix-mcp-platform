import type { Metadata } from "next";
import "bootstrap/dist/css/bootstrap.min.css";
import "bootstrap-icons/font/bootstrap-icons.css";
import "@/app/globals.css";

export const metadata: Metadata = {
  title: "Syndrix",
  description: "Internal AI capability hub for enterprises",
  icons: {
    icon: "/favicon.svg",
    shortcut: "/favicon.svg",
  },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" data-bs-theme="light">
      <head>
        <script dangerouslySetInnerHTML={{ __html: `try{localStorage.removeItem('mcp_theme');}catch(e){}` }} />
      </head>
      <body>{children}</body>
    </html>
  );
}
