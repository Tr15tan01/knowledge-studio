/**
 * A static rendering of the real answer + source-preview UI, filled with a clearly labelled
 * sample library. It uses the same typography and citation styles as the app.
 */
export function AnswerPreview() {
  return (
    <figure className="relative" aria-label="Illustration of an answer with citations, using a sample library">
      <div className="relative z-10 mr-4 sm:mr-10 rounded-[16px] border border-rule bg-surface p-5 pb-14 shadow-[var(--shadow-pop)] sm:p-6 sm:pb-14">
        <div className="flex justify-end">
          <div className="max-w-[85%] rounded-[14px] rounded-br-[4px] bg-ink px-3.5 py-2 text-sm text-paper">How did mountain honey reach the Tbilisi markets?</div>
        </div>
        <div className="prose-read mt-4 text-[0.98rem] leading-[1.65]">
          <p>
            Beekeepers carried chestnut honey down from the highland villages after the late-summer harvest
            <span className="cite-chip" data-active="true">1</span>, where traders sold it alongside cheese from the same pastures
            <span className="cite-chip">2</span>.
          </p>
          <p className="text-ink-2">
            <span className="font-sans text-[0.82rem] font-medium text-muted">Interpretation: </span>
            the two sources date the trade differently, so the timing is uncertain.
          </p>
        </div>
        <div className="mt-4 space-y-1 border-t border-rule pt-3 text-[0.82rem]">
          <div className="flex items-center gap-2">
            <span className="cite-chip !m-0">1</span>
            <span className="font-medium">A Field Guide to Mountain Honey</span>
            <span className="text-muted">p. 42</span>
          </div>
          <div className="flex items-center gap-2">
            <span className="cite-chip !m-0">2</span>
            <span className="font-medium">Markets of the Old Road</span>
            <span className="text-muted">p. 117</span>
          </div>
        </div>
      </div>
      <div className="relative z-20 -mt-10 ml-6 rounded-[14px] border border-rule bg-surface p-5 shadow-[var(--shadow-pop)] sm:ml-20">
        <div className="text-[0.8rem] text-muted">A Field Guide to Mountain Honey, chapter 3, p. 42</div>
        <p className="prose-read mt-2 text-[0.95rem] leading-[1.7]">
          <span className="text-muted">After the August harvest the combs were pressed in the village. </span>
          <span className="mark mark-sweep">The honey went down to the city markets on horseback, packed in clay jars sealed with beeswax.</span>
        </p>
      </div>
      <figcaption className="mt-3 text-center text-[0.76rem] text-faint">Illustration with a sample library. Your answers cite your own files.</figcaption>
    </figure>
  );
}
