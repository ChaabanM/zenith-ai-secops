import { Activity, AlertTriangle, FileText, LayoutDashboard, ShieldCheck, type LucideIcon } from "lucide-react";

export interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
}

export const NAV_ITEMS: NavItem[] = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard },
  { to: "/events", label: "Events", icon: Activity },
  { to: "/incidents", label: "Incidents", icon: AlertTriangle },
  { to: "/data-quality", label: "Data Quality", icon: ShieldCheck },
  { to: "/briefings", label: "Briefings", icon: FileText },
];
