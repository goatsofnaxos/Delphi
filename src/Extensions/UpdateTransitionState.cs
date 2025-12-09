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

    public IObservable<IDictionary<string, StateDefinition>> Process(IObservable<Tuple<Tuple<string, string>, Tuple<IDictionary<string, StateDefinition>, IDictionary<string, StateDefinition>>>> source)
    {
        return source.Select(value =>
        {
            var originalTransition = value.Item2.Item1;
            var updatedTransitionState = value.Item2.Item2.ToDictionary(entry => entry.Key, entry => new StateDefinition
            {
                Name = entry.Value.Name,
                OdorIndex = entry.Value.OdorIndex,
                TransitionsTo = entry.Value.TransitionsTo.ToList()
            });
            var requestedTransition = value.Item1.Item1;
            var initiatingState = value.Item1.Item2;

            // remove the requested transition from the transition state
            updatedTransitionState[initiatingState]
                .TransitionsTo.Remove(updatedTransitionState[initiatingState].TransitionsTo.Where(x => x == requestedTransition).First());

            // if the available transitions at that key are now empty, reset from the original transition dict
            if (updatedTransitionState[initiatingState].TransitionsTo.Count == 0)
            {
                Console.WriteLine("Exhausted transitions");
                updatedTransitionState[initiatingState] = originalTransition[initiatingState];
            }

            foreach (var kvp in updatedTransitionState)
            {
                Console.WriteLine("(" + kvp.Key + ")");
                Console.WriteLine("----");
                foreach (var v in kvp.Value.TransitionsTo)
                {
                    Console.WriteLine(v);
                }
            }

            return updatedTransitionState;
        });
    }
}
