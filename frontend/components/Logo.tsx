import { cn } from "@/lib/utils";

/** FieldOps wordmark shared by the web and mobile field-service experience. */
export function LogoMark({ size = 32, className }: { size?: number; className?: string }) {
  return (
    <div
      className={cn("brand-gradient flex items-center justify-center rounded-[28%] shadow-sm shadow-black/20", className)}
      style={{ width: size, height: size }}
    >
      <svg width={size * 0.56} height={size * 0.56} viewBox="0 0 24 24" fill="none">
        <path
          d="M4 5.5C4 4.67 4.67 4 5.5 4h13c.83 0 1.5.67 1.5 1.5v9c0 .83-.67 1.5-1.5 1.5H9l-4 4v-4H5.5C4.67 15 4 14.33 4 13.5v-8Z"
          fill="white"
          fillOpacity="0.92"
        />
        <path
          d="M12.5 6.8 13.6 9.4 16.2 10.5 13.6 11.6 12.5 14.2 11.4 11.6 8.8 10.5 11.4 9.4Z"
          className="fill-primary"
        />
      </svg>
    </div>
  );
}

export default function Logo({ size = 32, textClassName, className }: { size?: number; textClassName?: string; className?: string }) {
  return (
    <div className={cn("flex items-center gap-2.5", className)}>
      <LogoMark size={size} />
      <span className={cn("font-bold tracking-tight text-foreground", textClassName)}>
        Field<span className="text-primary">Ops</span>
      </span>
    </div>
  );
}
