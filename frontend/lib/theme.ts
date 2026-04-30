"use client";

import { useState, useEffect } from "react";

const KEY = "mcp_theme";

export function useTheme() {
  const [dark, setDark] = useState(false);

  useEffect(() => {
    const stored = localStorage.getItem(KEY);
    const isDark = stored === "dark";
    setDark(isDark);
    document.documentElement.setAttribute("data-bs-theme", isDark ? "dark" : "light");
  }, []);

  function toggle() {
    const next = !dark;
    setDark(next);
    localStorage.setItem(KEY, next ? "dark" : "light");
    document.documentElement.setAttribute("data-bs-theme", next ? "dark" : "light");
  }

  return { dark, toggle };
}
