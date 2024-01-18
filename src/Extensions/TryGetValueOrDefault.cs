using Bonsai;
using System;
using System.ComponentModel;
using System.Collections.Generic;
using System.Linq;
using System.Reactive.Linq;

[Combinator]
[Description("")]
[WorkflowElementCategory(ElementCategory.Transform)]
public class TryGetValueOrDefault
{
    public string Key {get; set;}
    public string DefaultString {get; set;}

    public IObservable<string> Process(IObservable<IDictionary<string, string>> source)
    {
        return source.Select(value => {
            string element;
            bool contained = value.TryGetValue(Key, out element);
            
            return contained ? element : DefaultString;
        });
    }
}
