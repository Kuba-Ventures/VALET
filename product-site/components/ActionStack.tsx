/**
 * Visual 02: a vertical stack of four mono chips with a cyan highlight that
 * travels down them in sequence (echoing the demo's step cascade).
 */
const CHIPS = ["Opens the app", "Finds the file", "Writes the reply", "Reports back"];

export default function ActionStack() {
  return (
    <div className="flex flex-col gap-3" aria-hidden>
      {CHIPS.map((chip, i) => (
        <div key={chip} className="act-chip" style={{ animationDelay: `${i * 1.1}s` }}>
          {chip}
        </div>
      ))}
    </div>
  );
}
