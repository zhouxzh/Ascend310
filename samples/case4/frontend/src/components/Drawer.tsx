import { useEffect, type ReactNode } from "react";
import { X } from "lucide-react";
import { IconButton } from "./ui";

interface DrawerProps {
  open: boolean;
  title: string;
  description?: string;
  onClose: () => void;
  children: ReactNode;
  width?: "regular" | "wide";
}

export function Drawer({ open, title, description, onClose, children, width = "regular" }: DrawerProps) {
  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose, open]);

  if (!open) return null;
  return (
    <div className="drawer-layer" role="presentation">
      <button className="drawer-layer__scrim" type="button" aria-label="关闭侧栏" onClick={onClose} />
      <aside className={`drawer drawer--${width}`} role="dialog" aria-modal="true" aria-label={title}>
        <header className="drawer__header">
          <div>
            <h2>{title}</h2>
            {description ? <p>{description}</p> : null}
          </div>
          <IconButton icon={X} label="关闭" onClick={onClose} />
        </header>
        <div className="drawer__body">{children}</div>
      </aside>
    </div>
  );
}
