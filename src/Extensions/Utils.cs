
using System;
using System.Collections.Generic;
using System.Linq;
using RuleSchema;

public static class Utils
{
    static Random rnd = new Random(DateTime.Now.Millisecond);

    public static string GetRandomState(List<StateProbability> stateProbabilities)
    {

        var totalProbability = stateProbabilities.Sum(x => 1.0 / x.Probability);
        if (totalProbability < 0.9999999999999 || totalProbability > 1.0000000000001)
        {
            throw new ArgumentException("Total probability must sum to 1");
        }

        var randomVal = rnd.NextDouble();
        foreach (StateProbability stateProbability in stateProbabilities)
        {
            var probability = 1.0 / stateProbability.Probability;
            if (randomVal < probability)
            {
                return stateProbability.Name;
            }   
            randomVal -= probability;
        }

        return null;
    }

    public static StateProbability GetUniformStateProbability(List<StateProbability> stateProbabilities)
    {
        return stateProbabilities.OrderBy(_ => Guid.NewGuid()).ToList()[0];
    }
}