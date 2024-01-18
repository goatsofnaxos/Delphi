using Bonsai;
using System;
using System.ComponentModel;
using System.Collections.Generic;
using System.Linq;
using System.Reactive.Linq;

[Combinator]
[Description("")]
[WorkflowElementCategory(ElementCategory.Transform)]
public class ContainsValue
{
    public string Element {get; set; }

    public IObservable<bool> Process(IObservable<IDictionary<string, string>> source)
    {
        return source.Select(value => value.Values.Contains(Element));
    }
}
