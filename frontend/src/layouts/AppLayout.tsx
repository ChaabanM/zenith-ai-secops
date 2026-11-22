import { Menu } from "lucide-react";
import { useEffect, useState } from "react";
import { Outlet, useLocation } from "react-router-dom";

import { BackendStatus } from "@/components/BackendStatus";
import { NAV_ITEMS } from "@/components/navigation";
import { Sidebar } from "@/components/Sidebar";
import { Button } from "@/components/ui/button";

export function AppLayout() {
  const { pathname } = useLocation();
  const [menuOpen, setMenuOpen] = useState(false);
  const current =
    NAV_ITEMS.find((item) => (item.to === "/" ? pathname === "/" : pathname === item.to || pathname.startsWith(`${item.to}/`)))?.label ?? "";

  useEffect(() => setMenuOpen(false), [pathname]);

  return (
    <div className="flex h-screen bg-background print:block print:h-auto">
      <div className="hidden md:flex print:hidden">
        <Sidebar />
      </div>
      {menuOpen && (
        <div className="fixed inset-0 z-50 flex md:hidden">
          <button className="absolute inset-0 bg-black/30" aria-label="Close navigation" onClick={() => setMenuOpen(false)} />
          <div className="relative flex">
            <Sidebar />
          </div>
        </div>
      )}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 items-center justify-between gap-2 border-b px-4 md:px-6 print:hidden">
          <div className="flex items-center gap-2">
            <Button variant="ghost" size="icon" className="md:hidden" aria-label="Open navigation" onClick={() => setMenuOpen(true)}>
              <Menu />
            </Button>
            <h1 className="text-sm font-medium text-muted-foreground">{current}</h1>
          </div>
          <BackendStatus />
        </header>
        <main className="flex-1 overflow-y-auto p-4 md:p-6 print:overflow-visible print:p-0">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
