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
public class TryLookupOrDefault
{
    public string DefaultState { get; set; }
    public string Key { get; set; }
    public IObservable<StateDefinition> Process(IObservable<IDictionary<string, StateDefinition>> source)
    {
        return source.Select(value =>
        {
            if (value.ContainsKey(Key))
            {
                return value[Key];
            } else
            {
                return value[DefaultState];
            }
        });
    }
}
