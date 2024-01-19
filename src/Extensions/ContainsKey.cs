using Bonsai;
using System;
using System.ComponentModel;
using System.Collections.Generic;
using System.Linq;
using System.Reactive.Linq;

[Combinator]
[Description("")]
[WorkflowElementCategory(ElementCategory.Transform)]
public class ContainsKey
{
    public string Key {get; set;}

    public IObservable<bool> Process(IObservable<IDictionary<string, string>> source)
    {
        return source.Select(value => value.ContainsKey(Key));
    }

    public IObservable<bool> Process(IObservable<IDictionary<string, int>> source)
    {
        return source.Select(value => value.ContainsKey(Key));
    }
}
