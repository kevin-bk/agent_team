import { FileImage } from "@/components/icons";
import { useEffect, useRef, useState } from "react";
import { useImageBlobUrl } from "@/api/hooks";
import { ImageLightbox } from "@/components/ImageLightbox";
import { cn } from "@/lib/utils";

/**
 * Render an inline image that may live behind an authenticated `/api` URL.
 *
 * A bare `<img src="/api/…">` can't attach the bearer token, so the request
 * falls back to cookie auth and can fail (e.g. JWK_KID_MISMATCH) — the picture
 * then breaks after a reload. This component instead fetches the bytes through
 * the API client (same bearer token as every other call) and shows an object
 * URL. `blob:`/`data:` sources (live optimistic sends) are used as-is.
 *
 * The fetch is deferred until the image scrolls into view so a long transcript
 * doesn't download every picture up front.
 */
export function AuthedImage({
  src,
  alt,
  className,
  zoomable = true,
}: {
  src: string;
  alt: string;
  className?: string;
  zoomable?: boolean;
}) {
  const direct = src.startsWith("blob:") || src.startsWith("data:");
  const containerRef = useRef<HTMLDivElement>(null);
  const [inView, setInView] = useState(direct);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (direct || inView) return;
    const el = containerRef.current;
    if (!el) return;
    const obs = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setInView(true);
          obs.disconnect();
        }
      },
      { rootMargin: "200px" },
    );
    obs.observe(el);
    return () => obs.disconnect();
  }, [direct, inView]);

  const query = useImageBlobUrl(direct ? undefined : src, inView);
  const resolved = direct ? src : query.data;

  if (!direct && query.isError) {
    return (
      <div
        className={cn(
          "flex h-24 w-24 items-center justify-center rounded-xl border border-border bg-surface-2 text-muted-foreground",
          className,
        )}
      >
        <FileImage className="h-5 w-5" />
      </div>
    );
  }

  if (!resolved) {
    return (
      <div
        ref={containerRef}
        className={cn(
          "h-24 w-24 animate-pulse rounded-xl border border-border bg-surface-2 motion-reduce:animate-none",
          className,
        )}
      />
    );
  }

  const img = (
    <img
      src={resolved}
      alt={alt}
      loading="lazy"
      decoding="async"
      className={className}
    />
  );

  if (!zoomable) return img;
  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="block cursor-zoom-in"
        aria-label={`Open ${alt}`}
      >
        {img}
      </button>
      {open && (
        <ImageLightbox
          src={resolved}
          name={alt}
          onClose={() => setOpen(false)}
        />
      )}
    </>
  );
}
