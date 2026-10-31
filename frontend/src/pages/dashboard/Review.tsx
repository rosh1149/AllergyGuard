import { ReviewQueue } from "../../components/ReviewQueue";
import { useDash } from "./context";

export function ReviewPage() {
  const { rid, canEdit } = useDash();
  return (
    <div>
      <h1>Uncertain matches</h1>
      <ReviewQueue
        base={`/restaurants/${rid}/review-queue`}
        canDecide={canEdit}
        intro="Dishes customers asked about that weren't on your menu, and dishes that matched more than one menu item. Customers were told the result was uncertain. Adding a missing dish or clearer names helps future checks."
      />
    </div>
  );
}
