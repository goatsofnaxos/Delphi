using Bonsai;
using System;
using System.ComponentModel;
using System.Collections.Generic;
using System.Linq;
using System.Reactive.Linq;
using RuleSchema;

[Combinator]
[Description("")]
[WorkflowElementCategory(ElementCategory.Transform)]
public class UpdateTransitionState
{
    public IObservable<IDictionary<string, List<StateProbability>>> Process(IObservable<Tuple<Tuple<StateProbability, string>, Tuple<IDictionary<string, List<StateProbability>>, IDictionary<string, List<StateProbability>>>>> source)
    {
        return source.Select(value =>
        {
            var originalTransition = value.Item2.Item1;
            var updatedTransitionState = value.Item2.Item2.ToDictionary(entry => entry.Key, entry => entry.Value.ToList());
            var requestedTransition = value.Item1.Item1;
            var initiatingState = value.Item1.Item2;

            Console.WriteLine("Iteration");
            Console.WriteLine(originalTransition.GetHashCode());
            Console.WriteLine(updatedTransitionState.GetHashCode());
            Console.WriteLine(updatedTransitionState[initiatingState].Count);

            // remove the requested transition from the transition state
            updatedTransitionState[initiatingState]
                .Remove(updatedTransitionState[initiatingState].Where(x => x.Name == requestedTransition.Name).First());

            // if the available transitions at that key are now empty, reset from the original transition dict
            if (updatedTransitionState[initiatingState].Count == 0)
            {
                updatedTransitionState[initiatingState] = originalTransition[initiatingState].ToList();
                Console.WriteLine(updatedTransitionState[initiatingState].Count);
            }

            return updatedTransitionState;
        });
    }
}
