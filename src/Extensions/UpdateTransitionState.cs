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
    public IObservable<IDictionary<string, List<string>>> Process(IObservable<Tuple<Tuple<string, string>, Tuple<IDictionary<string, List<string>>, IDictionary<string, List<string>>>>> source)
    {
        return source.Select(value =>
        {
            var originalTransition = value.Item2.Item1;
            var updatedTransitionState = value.Item2.Item2.ToDictionary(entry => entry.Key, entry => entry.Value.ToList());
            var requestedTransition = value.Item1.Item1;
            var initiatingState = value.Item1.Item2;

            // remove the requested transition from the transition state
            updatedTransitionState[initiatingState]
                .Remove(updatedTransitionState[initiatingState].Where(x => x == requestedTransition).First());

            // if the available transitions at that key are now empty, reset from the original transition dict
            if (updatedTransitionState[initiatingState].Count == 0)
            {
                updatedTransitionState[initiatingState] = originalTransition[initiatingState].ToList();
            }

            return updatedTransitionState;
        });
    }
}
