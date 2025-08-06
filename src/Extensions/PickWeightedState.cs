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
public class PickWeightedState
{
    public IObservable<string> Process(IObservable<List<StateProbability>> source)
    {
        return source.Select(value => Utils.GetRandomState(value));
    }
}