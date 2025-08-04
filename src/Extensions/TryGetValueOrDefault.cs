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
public class TryGetValueOrDefault
{
    public string Key {get; set;}
    public string DefaultString {get; set;}

    public IObservable<List<StateProbability>> Process(IObservable<IDictionary<string, List<StateProbability>>> source)
    {
        return source.Select(value => {
            List<StateProbability> element;
            bool contained = value.TryGetValue(Key, out element);
            
            return contained ? element : new List<StateProbability> {
                new StateProbability { Name = DefaultString, Probability = 1}
            };
        });
    }
}
