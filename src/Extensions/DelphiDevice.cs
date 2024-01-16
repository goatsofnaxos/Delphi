using Bonsai;
using Bonsai.Harp;
using System;
using System.Collections.Generic;
using System.Linq;
using System.Reactive.Linq;
using System.Text;
using System.Threading.Tasks;

namespace Extensions.Extensions
{
    [TypeVisualizer(typeof(DelphiDeviceVisualizer))]
    public class DelphiDevice : Sink<HarpMessage>
    {
        public override IObservable<HarpMessage> Process(IObservable<HarpMessage> source)
        {
            return source.Do(value => { });
        }
    }
}
