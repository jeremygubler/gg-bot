import { Announcements } from "../announcements/Announcements";
import { useTr } from "../i18n";

export function AnnouncementsPage() {
  const tr = useTr();
  return (
    <section>
      <h1 className="mb-4 text-2xl font-bold">{tr.t("ann.title")}</h1>
      <Announcements />
    </section>
  );
}
