import Link from "next/link";
import { Tagline } from "@/components/tagline";

export default function NotFound() {
  return (
    <div className="page-shell not-found">
      <p className="section-kicker">404</p>
      <h1>This page could not be found.</h1>
      <p className="hero-copy">The market moved on. Head back to the live board.</p>
      <Tagline slot="not-found" locale="en" />
      <Link className="primary-action" href="/">Back to the market</Link>
    </div>
  );
}
