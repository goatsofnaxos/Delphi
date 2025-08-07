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
public class UniformSampleStateProbabilities
{
    public IObservable<string> Process(IObservable<List<string>> source)
    {
        return source.Select(value => Utils.GetUniformStateProbability(value));
    }
}
